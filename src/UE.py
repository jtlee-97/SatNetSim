import math
import simpy
import random
import json # [추가] JSON 모듈
from Base import *
from config import *

"""
[UE State]
ACTIVE:                                         위성 연결, 정상 작동 상태
WAITING_RRC_CONFIGURATION:                      Serving 위성으로부터 HO COMMAND 대기 상태
RRC_CONFIGURED:                                 HO COMMAND 수신함, Target 위성과 연결을 시작하는 상태
WAITING_RRC_RECONFIGURATION_COMPLETE_RESOPNSE:  RRCRC 대기상태
INACITVE:                                       연결 없음
"""

class UE(Base):
    def __init__(self,
                 identity,
                 position_x,
                 position_y,
                 satellite_ground_delay,
                 serving_satellite,
                 env):

        # Config Initialization
        Base.__init__(self,
                      identity=identity,
                      position_x=position_x,
                      position_y=position_y,
                      env=env,
                      satellite_ground_delay=satellite_ground_delay,
                      object_type="UE")

        # UE 고유 속성 설정 - 초기 serving 위성
        self.serving_satellite = serving_satellite

        # Logic Initialization
        self.timestamps = []
        
        # Geometry_data_cache
        self.geometry_data_cache = {}

        self.messageQ = simpy.Store(env)
        self.cpus = simpy.Resource(env, UE_CPU)
        self.state = ACTIVE # 초기 상태: ACTIVE
        self.satellites = None 

        self.previous_serving_sat_id = None
        self.targetID = None
        self.retransmit_counter = 0

        # Running Process
        env.process(self.init())
        env.process(self.handle_messages())
        env.process(self.action_monitor())
        env.process(self.monitor_geometry())


    # =================== UE functions ======================
    # handle messages: satellite와 동일
    # 수신 메시지의 종류를 구분하거나, 처리 카운팅을 하는 것에 대해 구분되지 않음: UE에게는 그정도로 필요가 없음
    def handle_messages(self):
        while True:
            msg = yield self.messageQ.get()
            print(f"{self.type} {self.identity} start handling msg:{msg} at time {self.env.now}")
            data = json.loads(msg)
            self.env.process(self.cpu_processing(data))

    def cpu_processing(self, msg):
        with self.cpus.request() as request:
            task = msg['task']
            
            # Message Type: HO COMMAND
            # CURRENT UE STATE: WAITING_RRC_CONFIGURATION
            if task == HO_COMMAND:
                yield request # 대기
                satid = msg['from'] # Satellite ID CHECK
                
                # FIXME one error raised for serveing satellite is none, the suspect reason is synchronization issue with "switch to inactive"
                # Note that the UE didn't wait for the latest response for retransmission.
                # 명령 처리 중 연결이 끊켜 self.serving_satellite가 제거되는 경우, 문제가 발생할 수 있는 단계
                
                # WAITING_RRC_CONFIGURATION (HO CMD 대기상태) + 메시지 발신 위성이 기존 서빙 위성과 동일
                if self.state == WAITING_RRC_CONFIGURATION and satid == self.serving_satellite.identity:
                    targets = msg['targets'] # candidate satellite list
                    
                    # choose target
                    # TODO 최종 위성을 리스트의 첫번째 위성으로 선택 중 (현단계)
                    self.targetID = targets[0]
                    
                    self.state = RRC_CONFIGURED # State Change
                    self.previous_serving_sat_id = self.serving_satellite.identity # 이전 서빙 위성 ID 보관
                    self.retransmit_counter = 0 # 재전송 횟수 초기화
                    print(f"{self.type} {self.identity} receives the configuration at {self.env.now}") # Logging
                    
                    # 현재 시간, HO 성공 여부 기록
                    self.timestamps[-1]['timestamp'].append(self.env.now)
                    self.timestamps[-1]['isSuccess'] = True
                    
            # # Message Type: RRC RECONFIGURATION COMPLETE RESPONSE 
            # # CURRENT UE STATE: RRC_CONFIGURED
            # elif task == RRC_RECONFIGURATION_COMPLETE_RESPONSE:
            #     yield request
            #     satid = msg['from']
            #     satellite = self.satellites[satid] # all Satellite list check
                
            #     # TODO satid가 target cell인지 검증하는 절차가 확인으로 추가가 필요함
            #     if self.covered_by(satid): # using coverd_by function
            #         self.serving_satellite = satellite # msg trans. satellite
            #         self.state = ACTIVE # State Change
            #         self.timestamps[-1]['timestamp'].append(self.env.now) # Adding: for MIT
            #         print(f"{self.type} {self.identity} finished handover at {self.env.now}")
            
            elif task == RRC_ULGRANT:
                yield request
                satid = msg['from']
                target_satellite = self.satellites[satid] # all Satellite list check
                
                # TODO satid가 target cell인지 검증하는 절차가 확인으로 추가가 필요함
                if self.covered_by(satid): # using coverd_by function
                    self.serving_satellite = target_satellite # msg trans. satellite
                    self.state = ACTIVE # State Change
                    self.timestamps[-1]['timestamp'].append(self.env.now) # Adding: for MIT
                    print(f"{self.type} {self.identity} finished handover at {self.env.now}")
                    data = {
                        "task": RRC_RECONFIGURATION_COMPLETE, # RRC RECONFIGURATION COMPLETE 메시지 생성
                        "previous_id": self.previous_serving_sat_id,
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    print('Send RRC_RECONFIGURATION_COMPLETE message')

    # =================== Monitoring Process ======================
    # UE 상태를 모니터링, 필요한 경우 메시지를 전송
    # 주기적으로 send_request_condition() 함수(UE가 현재 위치에서 RRC 구성 요청을 보낼 수 있는지 여부를 판단)를 호출
    
    def monitor_geometry(self):
        # GEOMETRY_UPDATE_INTERVAL 마다 'for satid in self.satellites (전위성 순회)'
        # covered_by 필터링 후, get_geometry_info > geometry_data_cache 생성
        """
        [신규] 주기적으로 커버리지 내 위성들의 기하 정보를 계산하고 캐시에 저장합니다.
        향후 이 프로세스에서 RSRP 등 채널 품질 계산 로직이 추가됩니다.
        """
        while True:
            # self.satellites가 초기화될 때까지 대기
            if not self.satellites:
                yield self.env.timeout(10)
                continue

            # 커버리지 내에 있는 모든 위성에 대해 정보 업데이트
            covered_satellites = [sat_id for sat_id in self.satellites if self.covered_by(sat_id)]
            
            for sat_id in covered_satellites:
                satellite = self.satellites[sat_id]
                # 1. 기하 정보 계산
                geo_info = self.get_geometry_info(satellite)
                geo_info['ue_coords'] = (self.position_x, self.position_y)
                geo_info['sat_coords'] = (satellite.position_x, satellite.position_y)
                
                # 2. RSRP 계산
                rsrp_dbm = self.calculate_rsrp(geo_info)
                
                # TODO: 추후 SINR 등 추가 예정
                # ~
                
                # FIN: 캐시에 저장
                geo_info['rsrp'] = rsrp_dbm
                self.geometry_data_cache[sat_id] = geo_info
                
            # 정의된 시간 간격만큼 대기 (예: 100ms)
            yield self.env.timeout(GEOMETRY_UPDATE_INTERVAL)
    
    # cache 기반, 1ms 마다 행동하지만, geometry_data_cache를 기반으로 수행 (messageQ 처리로 1ms 주기 동작은 필요)
    # 보고서 기반 send_request_condition을 통해 measurement trigger를 판단
    def action_monitor(self):
        while True:
            # --- ACTION: Send Measurement Report ---
            if self.state == ACTIVE and self.send_request_condition(): # Condition               
                candidates = []
                
                # Candidate에 
                for satid in self.geometry_data_cache:
                    if satid != self.serving_satellite.identity:
                        candidates.append(satid)
                
                # NOTE: Previous code
                for satid in self.satellites: # 모든 위성 순회
                    if self.covered_by(satid) and satid != self.serving_satellite.identity: # Condition: in Cover + not Serving Satellite
                        candidates.append(satid)
                                                
                # Prepare Measurement Report message
                data = {
                    "task": MEASUREMENT_REPORT,
                    "candidate": candidates,
                }
                
                # Case: Candidate Satellite List Not Empty (at lease 1 over)
                if len(candidates) != 0:
                    # NOTE: Previous check code (delete) 
                    # self.print_geometry_info([self.serving_satellite.identity] + candidates)
                    
                    # NOTE: [TEST] 기하(거리, 각도 등) 정보 출력용 (monitor_geometry process를 통한 cache 기반 로그)
                    print(f"--- [UE {self.identity} Cached Geometry at {self.env.now:.2f}s] ---")
                    # 서빙 위성과 후보 위성 ID 목록
                    ids_to_print = [self.serving_satellite.identity] + candidates
                    
                    # NOTE: TRACING: 캐싱 데이터 출력
                    for sat_id in ids_to_print:
                        if sat_id in self.geometry_data_cache:
                            cached_info = self.geometry_data_cache[sat_id]
                            print(f"  Satellite {sat_id}:")
                            print(f"    - UE Coords : ({cached_info['ue_coords'][0]:.2f}, {cached_info['ue_coords'][1]:.2f})")
                            print(f"    - Sat Coords: ({cached_info['sat_coords'][0]:.2f}, {cached_info['sat_coords'][1]:.2f})")
                            print(f"    - Dist      : {cached_info['distance']:.2f} m")
                            print(f"    - Elev      : {cached_info['elevation_angle']:.2f} deg")
                            print(f"    - RSRP      : {cached_info['rsrp']:.2f} dBm")
                        else:
                            print(f"  Satellite {sat_id}: No data in cache.")
                    print("----------------------------------------------------------")
                    
                    # Message Send Protocol Start
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    self.timestamps.append({'timestamp' : [self.env.now]}) # Logging
                    self.timestamps[-1]['from'] = self.serving_satellite.identity # Logging
                    self.timer = self.env.now # RE-TRANSMIT TIMER START
                    self.state = WAITING_RRC_CONFIGURATION # UE STATE CHANGE
                                    
            # --- ACTION: Trigger retransmission if conditions are met ---
            if RETRANSMIT and self.state == WAITING_RRC_CONFIGURATION \
            and (self.env.now - self.timer) > RETRANSMIT_THRESHOLD \
            and self.retransmit_counter < MAX_RETRANSMIT:
                # NOTE: Retransmission conditions
                # 1. RETRANSMIT enabled (see config.py)
                # 2. UE state is WAITING_RRC_CONFIGURATION
                # 3. Timer exceeded threshold: now - timer > RETRANSMIT_THRESHOLD
                # 4. Retransmission attempts < MAX_RETRANSMIT

                self.timer = self.env.now # retransmit timer reset
                self.timestamps[-1]['timestamp'].append(self.env.now) # Logging
                # NOTE: 현시점 Re-transmit +1회 실시
                
                # Message Send Restart
                candidates = []
                for satid in self.satellites:
                    if self.covered_by(satid) and satid != self.serving_satellite.identity:
                        candidates.append(satid)
                data = {
                    "task": RETRANSMISSION, # message type은 MR이 아닌 재전송으로 변경
                    "candidate": candidates
                }
                if len(candidates) != 0:
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=self.serving_satellite.messageQ,
                            to=self.serving_satellite
                        )
                    )
                    self.retransmit_counter += 1 # counter add
            
                    
            # --- ACTION: RANDOM ACCESS Procedure ---
            if self.state == RRC_CONFIGURED:  # Condition: RRC_CONFIGURED (HO CMD 수신 상태)
                if self.targetID and self.covered_by(self.targetID): # CHECK
                    target = self.satellites[self.targetID]
                    data = {
                        "task": RRC_RANDOM_ACCESS, # RRC RECONFIGURATION COMPLETE 메시지 생성
                    }
                    self.env.process(
                        self.send_message(
                            delay=self.satellite_ground_delay,
                            msg=data,
                            Q=target.messageQ,
                            to=target
                        )
                    )
                    self.state = WAITING_RRC_ULGRANT # STATE CHANGE
                    
            # Switch to INACTIVE State
            if self.serving_satellite is not None and self.outside_coverage():
                # TODO: RLF, HOF 등이 발생하는 경우가 outside_coverage()가 되야함
                print(f"UE {self.identity} lost connection at time {self.env.now} from satellite {self.serving_satellite.identity}") # Logging
                self.serving_satellite = None # serv_idx none
                
                # ACTIVE/HO CMD 대기 상태에서
                if self.state == ACTIVE or self.state == WAITING_RRC_CONFIGURATION:
                    if self.state == WAITING_RRC_CONFIGURATION:
                        print(f"UE {self.identity} handover failure at time {self.env.now}") # Logging
                        self.timestamps[-1]['timestamp'].append(self.env.now) # Logging
                        self.timestamps[-1]['isSuccess'] = False # Logging
                    self.state = INACTIVE # STATE CHANGE
                                
            # 1ms 대기: 1회의 action_monitor 이후, 제어권 인계 (1ms 주기의 모니터링 주기)
            yield self.env.timeout(1)
            

    # ==================== Utils (Not related to Simpy) =============
    def covered_by(self, satelliteID):
        # TODO: 필터링 대상 (현: 거리기반 / 후: RSRP 기반 필터링 구현 필요, 혹은 주변 검사 후 다음단계로 삽입 등 고려)
        satellite = self.satellites[satelliteID]     
        # UE와 위성의 2D 지상 거리를 계산
        d = math.sqrt(((self.position_x - satellite.position_x) ** 2) +
                    ((self.position_y - satellite.position_y) ** 2))
        return d <= 50000 # 50 km 반경 (약 1-tiers)
    
    # -- Previous 'covered_by' code (25.08.12) --
    # def covered_by(self, satelliteID): # UE가 특정 위성 커버리지 내에 있는가 확인하는 함수
    #     # TODO: 필터링 대상 (주변 1-tiers 셀만 서칭하도록 수정 필요)
    #     satellite = self.satellites[satelliteID] # satelliteID에 해당하는 위성 객체를 가져옴
    #     # 두 점 (UE, 위성) 사이의 거리를 피타고라스 정리를 사용하여 계산 (직선거리)
    #     d = math.sqrt(((self.position_x - satellite.position_x) ** 2) + (
    #             (self.position_y - satellite.position_y) ** 2)) 
    #     return d <= SATELLITE_R # Ture/False 반환

    # ----------------------------------------------
    # 전체 위성 군 중, Request가 가능한 위성 탐색
    # Measurement Trigger (preparation event)가 추가되어야 하는 위치
    def send_request_condition(self):
        # 캐시에 서빙 위성 정보가 없으면 False 반환
        if self.serving_satellite.identity not in self.geometry_data_cache:
            return False
        # 캐시에서 서빙 위성과의 거리를 가져옴
        d1_serve = self.geometry_data_cache[self.serving_satellite.identity]['distance']
        # 다른 위성들도 캐시에서 거리 정보를 가져와 비교
        for satid, geo_info in self.geometry_data_cache.items():
            if satid == self.serving_satellite.identity:
                continue
            d_neighbor = geo_info['distance']
            # 캐시된 데이터를 기반으로 핸드over 여부 결정
            if d_neighbor + 100 < d1_serve:
                return True  
        return False
    
    # # 전체 위성 군 중, Request가 가능한 위성 탐색
    # def send_request_condition(self):
    #     p = (self.position_x, self.position_y) # UE의 현재 위치 p 변수에 저장
    #     d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y)) # d1_serve 변수에 현재 UE와 서비스 중인 위성 간의 거리 저장
    #     for satid in self.satellites: # 모든 위성을 히나씩 순회하며 검사 시작
    #         satellite = self.satellites[satid] # 현재 순회 대상이된 위성 객체 전체를 satellite 변수에 저장
    #         d = math.dist(p, (satellite.position_x, satellite.position_y)) # 순회 대상 위성 객체와 현재 UE 간의 거리 계산
    #         # 조건문 실행 (타겟 위성 검토)
    #         # 이웃 위성까지의 거리 d가 현재 서빙 위성거라 d1_serve보다 100미터 이상 작을 경우, True 반환
    #         # 100 미터는 offset으로 적용된 값으로, 이웃 위성까지의 거리와 현재 서빙 위성까지의 거리가 충분히 멀어야 한다는 의미 (PP 방지)
    #         if d + 100 < d1_serve:
    #             return True
    #     return False

    # TODO: RLF를 기반으로 outside_coverage를 구성해야함 (단순 영역을 벗어나는 것이 아님)
    def outside_coverage(self):
        p = (self.position_x, self.position_y)
        d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y))
        # TODO We may want to remove the second condition someday...
        return d1_serve >= SATELLITE_R and self.position_x < self.serving_satellite.position_x

    # ==================== Channel Model Helper Functions (from MATLAB) ======================
    def _los_prob(self, elevation_angle):
        """ MATLAB의 los_prob 함수를 변환. 고도각에 따른 LoS 확률을 반환 """
        # 고도각(0-90도)을 0-8 인덱스로 변환
        idx = max(0, min(round(elevation_angle / 10) -1, 8))
        if ENVIRONMENT_TYPE == 'RURAL':
            return RURAL_LOS_PROB[idx]
        # TODO: 다른 환경 타입에 대한 값도 추가 가능
        return RURAL_LOS_PROB[idx] # 기본값

    def _sd_cl(self, elevation_angle):
        """ MATLAB의 sd_cl 함수를 변환. LoS/NLoS 상황의 섀도잉 및 클러터 손실을 반환 """
        idx = max(0, min(round(elevation_angle / 10) -1, 8))
        if ENVIRONMENT_TYPE == 'RURAL':
            los_std = RURAL_LOS_SHADOW_STD[idx]
            nlos_std = RURAL_NLOS_SHADOW_STD[idx]
            nlos_cl = RURAL_NLOS_CLUTTER_LOSS[idx]
        else: # 기본값
            los_std = RURAL_LOS_SHADOW_STD[idx]
            nlos_std = RURAL_NLOS_SHADOW_STD[idx]
            nlos_cl = RURAL_NLOS_CLUTTER_LOSS[idx]

        # MATLAB의 randn(정규분포 난수)을 Python의 random.gauss로 대체
        los_shadowing = los_std * random.gauss(0, 1)
        nlos_shadowing_and_clutter = nlos_std * random.gauss(0, 1) + nlos_cl
        
        return los_shadowing, nlos_shadowing_and_clutter

    def _freespacePL(self, freq_hz, dist_m):
        """ MATLAB의 freespacePL 함수를 변환. """
        if dist_m == 0:
            return 0
        # MATLAB 공식: 20*log10(f) + 20*log10(d) + 20*log10(4*pi/c)
        # 단위를 Hz, m로 사용
        return 20 * math.log10(dist_m) + 20 * math.log10(freq_hz) - 147.55
    
    def _calculate_path_loss(self, geo_info):
        """
        MATLAB의 GET_LOSS 로직을 구현. 
        LoS/NLoS 확률을 적용하여 최종 경로 손실을 계산합니다.
        """
        dist_m = geo_info['distance']
        elev_angle = geo_info['elevation_angle']
        freq_hz = SC9_CARRIER_FREQUENCY_HZ * 1_000_000 # MHz를 Hz로 변환

        # 1. FSPL(자유 공간 경로 손실) 계산
        fspl = self._freespacePL(freq_hz, dist_m)
        
        # 2. LoS 확률 계산
        los_probability = self._los_prob(elev_angle)
        
        # 3. 섀도잉 및 클러터 손실 계산
        los_loss_component, nlos_loss_component = self._sd_cl(elev_angle)
        
        # 4. LoS/NLoS 확률을 가중치로 하여 최종 손실 계산
        # Loss = (Prob_LoS/100 * (FSPL + LoS_loss)) + ((100-Prob_LoS)/100 * (FSPL + NLoS_loss))
        total_loss = (los_probability / 100) * (fspl + los_loss_component) + \
                     ((100 - los_probability) / 100) * (fspl + nlos_loss_component)
                     
        return total_loss

    def calculate_rsrp(self, geo_info):
        """
        최종 RSRP 계산을 위한 메인 함수. (MATLAB의 RB, RS 개념 반영)
        경로 손실을 계산하고, 송신 파워 및 안테나 이득을 적용합니다.
        """
        # 1. 전체 경로 손실 계산 (기존과 동일)
        path_loss = self._calculate_path_loss(geo_info)
        
        # 2. RB당 송신 파워 계산 (MATLAB 로직 반영)
        # 전체 Tx 파워를 RB 개수로 나눔 (dB 스케일에서는 빼기)
        tx_power_per_rb_dbm = SC9_SATELLITE_TXPW_dBm - 10 * math.log10(NUM_RESOURCE_BLOCKS)
        
        # 3. 최종 RSRP 계산 (MATLAB 로직 반영)
        # RSRP = (RB당 파워) + (모든 안테나 이득) - (경로 손실) - (RS Factor)
        # TODO: Satellite의 Tx Antenna Gain은 추가 함수 구현 후 반영이 필요한 상태
        rsrp_dbm = tx_power_per_rb_dbm + SC9_HANDHELD_RXGAIN \
                   - path_loss - 10 * math.log10(REFERENCE_SIGNAL_FACTOR)
        
        return rsrp_dbm

    # ==================== Geometry Calculation Functions ======================
    def get_geometry_info(self, satellite):
        """
        특정 위성과의 기하학적 정보(거리, 고도각, 안테나 각도)를 한번에 계산하여 반환합니다.

        Args:
            satellite (Satellite): 정보를 계산할 대상 위성 객체

        Returns:
            dict: 거리(m), 고도각(degree), 안테나 각도(degree)를 포함하는 딕셔너리
        """
        # --- 1. 직선 거리 (Slant Distance) 계산 ---
        # 지상에서의 2D 거리 (dx, dy)와 고도 차이(dz)를 이용해 3D 직선 거리를 계산합니다.
        dx = self.position_x - satellite.position_x
        dy = self.position_y - satellite.position_y
        dz = SC9_HANDHELD_ALTITUDE - SC9_SATELLITE_ALTITUDE
        
        slant_distance = math.sqrt(dx**2 + dy**2 + dz**2) # SATELLITE-UE 3D Distance

        # --- 2. 고도각 (Elevation Angle) 계산 ---
        # 지구 중심, UE, 위성이 이루는 삼각형에 사인 법칙을 적용한 공식입니다.
        try:
            # asin의 입력값은 -1과 1 사이여야 하므로, 부동소수점 오류 방지를 위해 clamp 처리
            arg = (SC9_SATELLITE_ALTITUDE**2 + 2*SC9_SATELLITE_ALTITUDE*EARTH_RADIUS - slant_distance**2) / (2*slant_distance*EARTH_RADIUS)
            arg = max(-1.0, min(1.0, arg))
            elevation_angle = math.degrees(math.asin(arg))
        except ValueError:
            elevation_angle = -90 # 계산 불가능한 경우 (예: 위성이 지구 반대편)

        # --- 3. 안테나 각도 (Antenna Angle / Off-boresight Angle) 계산 ---
        # 위성 안테나의 중심(Boresight, Nadir)에서 UE가 얼마나 벗어나 있는지를 나타내는 각도입니다.
        try:
            # atan2는 acos에 비해 수치적으로 더 안정적이며 입력값 범위를 제한할 필요가 없습니다.
            horizontal_distance = math.sqrt(dx**2 + dy**2)
            antenna_angle = math.degrees(math.atan2(horizontal_distance, abs(dz)))
        except ValueError:
            antenna_angle = 90 # 계산 불가능한 경우

        return {
            "distance": slant_distance,
            "elevation_angle": elevation_angle,
            "antenna_angle": antenna_angle
        }

            
    # 이전 사용하던 실시간 계산 기반 geo 정보 확인 메서드 (25.08.12)
    # def print_geometry_info(self, satellite_ids):
    #     """
    #     주어진 위성 ID 목록에 대한 기하학적 정보를 계산하고 출력합니다.
    #     """
    #     if not self.satellites:
    #         print(f"--- [UE {self.identity} Geometry Info] Satellites not available. ---")
    #         return

    #     print(f"--- [UE {self.identity} Geometry Info at {self.env.now:.2f}s] ---")
    #     for sat_id in satellite_ids:
    #         if sat_id in self.satellites:
    #             satellite = self.satellites[sat_id]
    #             geo_info = self.get_geometry_info(satellite)
    #             print(f"  Satellite {sat_id}:\n    - UE Coords: ({self.position_x:.2f}, {self.position_y:.2f})\n    - Satellite Coords: ({satellite.position_x:.2f}, {satellite.position_y:.2f})\n    - Slant Distance: {geo_info['distance']:.2f} m\n    - Elevation Angle: {geo_info['elevation_angle']:.2f} degrees\n    - Antenna Angle: {geo_info['antenna_angle']:.2f} degrees")
    #         else:
    #             print(f"  Satellite {sat_id}: Not available.")
    #     print("----------------------------------------------------")
    



#----------------------------------------------
# TODO
# 현단계: 
# 1) 거리, 고각, 안테나 각도 계산 반영
# 2) 이는 특정 주기마다 수행되도록 하며 캐싱에 저장
# 3) action_monitor는 캐싱 정보를 기반으로 수행 (특히 Measurement Report에 대해)
#
# 다음 단계:
# 1) 채널 모델을 거리, 고각, 안테나 각도 주기에 맞춰 계산 (진행 완료)
# 1-1) 검산 과정 필요 (MATLAB으로 교차 검증해볼 것: 2025.08.13. 00:29 / geo_channel_phase1_250813 커밋버전)
        """
        --- [UE 1 Cached Geometry at 6700.00s] ---
            Satellite 1:
                - UE Coords : (-16974.48, 8132.79)
                - Sat Coords: (644.44, 0.00)
                - Dist      : 600313.73 m
                - Elev      : 88.06 deg
                - RSRP      : -313.52 dBm
            Satellite 2:
                - UE Coords : (-16974.48, 8132.79)
                - Sat Coords: (-30605.56, 0.00)
                - Dist      : 600209.92 m
                - Elev      : 88.41 deg
                - RSRP      : -314.08 dBm
        -------------------------------------------
        """
#
# 2) 안테나 페턴 함수를 별도로 만들고 이를 여기에 반영시킴
# 3) config.py의 파워, 이득 등을 고려해서 DL에 대한 원신호 계산을 수행 (RSRP)
# 4) 지금까지는 필터링된 위성군에게만 적용해 계산되었으나, Interference 계산을 위해서는 전위성 군에 대한 조건부 스캔 확장 필요
# 5) 이를 모두 반영해서 RSRP, SNR, SINR 등을 계산할 수 있도록 확장
