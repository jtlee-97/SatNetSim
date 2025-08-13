import math
import simpy
import random
from scipy.special import jv
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
        self.handover_cooldown_end_time = -1

        # Running Process
        env.process(self.init())
        env.process(self.MESSAGE_CONTROL())
        env.process(self.ACTION_MONITOR())
        env.process(self.GEOMETRY_MONITOR())


    # =================== UE functions ======================
    # handle messages: satellite와 동일
    # 수신 메시지의 종류를 구분하거나, 처리 카운팅을 하는 것에 대해 구분되지 않음: UE에게는 그정도로 필요가 없음
    def MESSAGE_CONTROL(self):
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
    def GEOMETRY_MONITOR(self):
        # GEOMETRY_UPDATE_INTERVAL 마다 'for satid in self.satellites (전위성 순회)'
        # covered_by 필터링 후, get_geometry_info > geometry_data_cache 생성
        while True:
            if not self.satellites:
                yield self.env.timeout(10)
                continue

            # 1. 정밀 탐색 대상 위성 목록 필터링 (50km 반경)
            covered_sat_ids = [sat_id for sat_id in self.satellites if self.covered_by(sat_id)]
            
            # 임시 저장소: 이번 타임스텝에 계산된 모든 RSRP 값을 보관
            all_rsrps_in_scope = {}

            # 2. 모든 탐색 대상 위성에 대해 RSRP 우선 계산
            for sat_id in covered_sat_ids:
                satellite = self.satellites[sat_id]
                
                geo_info = self.get_geometry_info(satellite)
                geo_info['ue_coords'] = (self.position_x, self.position_y)
                geo_info['sat_coords'] = (satellite.position_x, satellite.position_y)
                
                channel_details = self.calculate_rsrp(geo_info)
                
                final_entry = {**geo_info, **channel_details}
                self.geometry_data_cache[sat_id] = final_entry
                all_rsrps_in_scope[sat_id] = final_entry['rsrp']

            # 3. 계산된 RSRP들을 바탕으로 각 위성의 SINR 계산 및 캐시 업데이트
            for sat_id in covered_sat_ids:
                # '신호'는 현재 위성의 RSRP
                signal_rsrp = all_rsrps_in_scope[sat_id]
                
                # '간섭'은 현재 위성을 제외한 나머지 모든 위성들의 RSRP 리스트
                interference_list = [rsrp for other_id, rsrp in all_rsrps_in_scope.items() if other_id != sat_id]
                
                # SINR 계산
                sinr, noise = self._calculate_sinr(signal_rsrp, interference_list)
                
                # 계산된 SINR을 캐시에 추가
                self.geometry_data_cache[sat_id]['sinr'] = sinr
                self.geometry_data_cache[sat_id]['noise'] = noise
                
            yield self.env.timeout(GEOMETRY_UPDATE_INTERVAL)
    
    
    # cache 기반, 1ms 마다 행동하지만, geometry_data_cache를 기반으로 수행 (messageQ 처리로 1ms 주기 동작은 필요)
    # 보고서 기반 send_request_condition을 통해 measurement trigger를 판단
    def ACTION_MONITOR(self):
        while True:
            # --- ACTION: Send Measurement Report ---
            # --- 서빙 위성 제외, 후보셀들에 대해서만 판별
            
            # --Rollback Point--
            # if self.state == ACTIVE and self.send_request_condition_A3(): 
            if self.state == ACTIVE and self.env.now >= self.handover_cooldown_end_time and self.send_request_condition_A3():              
                candidate_measurements = []
                for sat_id, cached_info in self.geometry_data_cache.items():
                    if sat_id == self.serving_satellite.identity:
                        continue  # 서빙 위성은 후보가 아니므로 제외
                
                # 보고서에 포함할 측정 정보 딕셔너리 생성
                    measurement_entry = {
                        "id": sat_id,
                        "ue_coords": cached_info['ue_coords'],
                        "sat_coords": cached_info['sat_coords'],
                        "distance": cached_info['distance'],
                        "elevation_angle": cached_info['elevation_angle'],
                        "antenna_angle": cached_info['antenna_angle'],
                        "rsrp": cached_info['rsrp'],
                        "sinr": cached_info.get('sinr', -999) # 안전을 위해 .get() 사용
                    }
                    candidate_measurements.append(measurement_entry)
               
                # Case: Candidate Satellite List Not Empty (at lease 1 over)
                if len(candidate_measurements) > 0:
                    # Prepare Measurement Report message
                    data = {
                        "task": MEASUREMENT_REPORT,
                        "candidate_measurements": candidate_measurements,
                    }
                    
                    # 전송 메시지 정보 출력
                    print(f"--- [UE {self.identity} sends Measurement Report to Satellite {self.serving_satellite.identity} at {self.env.now:.2f}s] ---")
                    print(json.dumps(data, indent=4))
                    print("----------------------------------------------------------")
                    
                    # NOTE: [TEST] 기하(거리, 각도 등) 정보 출력용 (GEOMETRY_MONITOR process를 통한 cache 기반 로그)
                    print(f"--- [UE {self.identity} Cached Geometry at {self.env.now:.2f}s] ---")
                    
                    candidate_ids = [entry['id'] for entry in candidate_measurements]
                    ids_to_print = [self.serving_satellite.identity] + candidate_ids
                    
                    # NOTE: TRACING: 캐싱 데이터 출력
                    for sat_id in ids_to_print:
                        if sat_id in self.geometry_data_cache:
                            cached_info = self.geometry_data_cache[sat_id]
                            print(f"  Satellite {sat_id}:")
                            print(f"   - Coords    : UE({cached_info['ue_coords'][0]:.2f}, {cached_info['ue_coords'][1]:.2f}) | Sat({cached_info['sat_coords'][0]:.2f}, {cached_info['sat_coords'][1]:.2f})")
                            print(f"   - Geometry  : Dist={cached_info['distance']:.2f}m | Elev={cached_info['elevation_angle']:.2f} degree | Ant_Angle={cached_info['antenna_angle']:.2f} degree")
                            print(f"   - Path Loss : Total={cached_info['basic_path_loss']:.2f}dB (FSPL={cached_info['fspl']:.2f}, LoS Prob={cached_info['los_prob']:.1f}%)")
                            print(f"   - RSRP Comp : TxPwr_RB={cached_info['tx_power_per_rb_dbm']:.2f}dBm | SatGain={cached_info['sat_tx_gain_dbi']:.2f}dBi | UEGain={cached_info['ue_rx_gain_dbi']:.2f}dBi")
                            if 'sinr' in cached_info:
                                print(f"   - Quality   : RSRP={cached_info['rsrp']:.2f} dBm | SINR={cached_info['sinr']:.2f} dB")
                                # [수정] 캐시에 저장된 Noise 값 출력
                                print(f"   - Noise     : Thermal Noise={cached_info['noise']:.2f} dBm")
                            else:
                                print(f"   - RSRP Final: {cached_info['rsrp']:.2f} dBm")
                    print("----------------------------------------------------------")
                    # --------- TRACING END ---------#
                    
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
            # -- Rollback Point --
            # if RETRANSMIT and self.state == WAITING_RRC_CONFIGURATION \
            # and (self.env.now - self.timer) > RETRANSMIT_THRESHOLD \
            # and self.retransmit_counter < MAX_RETRANSMIT:
            if RETRANSMIT and self.state == WAITING_RRC_CONFIGURATION and (self.env.now - self.timer) > RETRANSMIT_THRESHOLD and self.retransmit_counter < MAX_RETRANSMIT:
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
            # -- Rollback Point: RACH는 건들지 않음 --
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
                    
            # -- Rollback Point --
            # --- [핵심 수정] SINR 기반의 새로운 연결 종료 로직 ---
            if self.state == ACTIVE and self.serving_satellite and \
               self.serving_satellite.identity in self.geometry_data_cache:
                
                # 조건 1: 서빙셀의 SINR이 매우 나쁜가?
                serving_sinr = self.geometry_data_cache[self.serving_satellite.identity]['sinr']
                if serving_sinr <= THRESHOLD_Q_OUT:
                    
                    # 조건 2: 갈아탈 만한 다른 좋은 위성이 없는가?
                    # 이웃 위성들의 SINR 리스트를 생성
                    neighbor_sinrs = [info['sinr'] for sat_id, info in self.geometry_data_cache.items() \
                                      if sat_id != self.serving_satellite.identity]
                    
                    # all() 함수는 모든 항목이 조건에 맞아야 True. 즉, 모든 이웃의 SINR이 -6dB보다 낮은지 확인
                    if all(sinr < THRESHOLD_Q_IN for sinr in neighbor_sinrs):
                        print(f"--- UE {self.identity} Connection Lost at {self.env.now:.2f}s ---")
                        print(f"    Serving SINR ({serving_sinr:.2f} dB) <= Threshold ({THRESHOLD_Q_OUT} dB)")
                        print(f"    AND No suitable neighbor found.")
                        
                        self.serving_satellite = None
                        self.state = INACTIVE

            # # Switch to INACTIVE State
            # if self.serving_satellite is not None and self.outside_coverage():
            #     # TODO: RLF, HOF 등이 발생하는 경우가 outside_coverage()가 되야함
            #     print(f"UE {self.identity} lost connection at time {self.env.now} from satellite {self.serving_satellite.identity}") # Logging
            #     self.serving_satellite = None # serv_idx none
                
            #     # ACTIVE/HO CMD 대기 상태에서
            #     if self.state == ACTIVE or self.state == WAITING_RRC_CONFIGURATION:
            #         if self.state == WAITING_RRC_CONFIGURATION:
            #             print(f"UE {self.identity} handover failure at time {self.env.now}") # Logging
            #             self.timestamps[-1]['timestamp'].append(self.env.now) # Logging
            #             self.timestamps[-1]['isSuccess'] = False # Logging
            #         self.state = INACTIVE # STATE CHANGE
                                
            # 1ms 대기: 1회의 ACTION_MONITOR 이후, 제어권 인계 (1ms 주기의 모니터링 주기)
            yield self.env.timeout(1)
            

    # ==================== Utils (Not related to Simpy) =============
    # -- RollBack Point --
    # def covered_by(self, satelliteID):
    #     # TODO: 필터링 대상 (현: 거리기반 / 후: RSRP 기반 필터링 구현 필요, 혹은 주변 검사 후 다음단계로 삽입 등 고려)
    #     satellite = self.satellites[satelliteID]     
    #     # UE와 위성의 2D 지상 거리를 계산
    #     d = math.sqrt(((self.position_x - satellite.position_x) ** 2) +
    #                 ((self.position_y - satellite.position_y) ** 2))
    #     return d <= 50000
    def covered_by(self, satelliteID):
        # TODO: 필터링 대상 (현: 거리기반 / 후: RSRP 기반 필터링 구현 필요, 혹은 주변 검사 후 다음단계로 삽입 등 고려)
        satellite = self.satellites[satelliteID]     
        # UE와 위성의 2D 지상 거리를 계산
        d = math.sqrt(((self.position_x - satellite.position_x) ** 2) +
                    ((self.position_y - satellite.position_y) ** 2))
        return d <= 1.5 * SATELLITE_R

    def send_request_condition_A3(self):
        # 서빙 위성 정보가 캐시에 없으면 결정 불가
        if self.serving_satellite.identity not in self.geometry_data_cache:
            return False
            
        # 캐시에서 서빙 위성의 SINR 값을 가져옴
        sinr_serving = self.geometry_data_cache[self.serving_satellite.identity]['sinr']

        # 다른 위성들(이웃 위성)의 SINR과 비교
        for satid, cached_info in self.geometry_data_cache.items():
            if satid == self.serving_satellite.identity:
                continue
            
            sinr_neighbor = cached_info['sinr']
            
            # A3 event: 이웃 위성의 SINR이 서빙 위성보다 일정 수준(A3_OFFSET) 이상 강해지면 True 반환
            if sinr_neighbor > sinr_serving + A3_OFFSET:
                print(f"Handover Triggered: Neighbor {satid} (SINR {sinr_neighbor:.2f} dB) > Serving {self.serving_satellite.identity} (SINR {sinr_serving:.2f} dB)")
                return True
                
        return False

    # -- Rollback Point --
    # # TODO: RLF를 기반으로 outside_coverage를 구성해야함 (단순 영역을 벗어나는 것이 아님)
    # def outside_coverage(self):
    #     p = (self.position_x, self.position_y)
    #     d1_serve = math.dist(p, (self.serving_satellite.position_x, self.serving_satellite.position_y))
    #     # TODO We may want to remove the second condition someday...
    #     return d1_serve >= 1.25*SATELLITE_R #and self.position_x < self.serving_satellite.position_x

    # ==================== Channel Model Helper Functions (from MATLAB) ======================
    def _los_prob(self, elevation_angle):
        """ MATLAB의 los_prob 함수를 변환. 고도각에 따른 LoS 확률을 반환 """
        # 고도각(0-90도)을 0-8 인덱스로 변환
        idx = max(0, min(round(elevation_angle / 10) -1, 8))
        if ENVIRONMENT_TYPE == 'RURAL':
            return RURAL_LOS_PROB[idx]
        # TODO: 다른 환경 타입에 대한 값도 추가 가능
        
        # The code is a Python snippet that includes a debug print statement using the `print`
        # function. It is printing out a debug message that includes the current simulation time
        # (`self.env.now`), the elevation angle (`elevation_angle`), the index (`idx`), and the
        # line-of-sight probability (`RURAL_LOS_PROB`). The message is formatted using f-strings in
        # Python to include the values of these variables. This debug message is likely used for
        # troubleshooting and monitoring the simulation process.
        # NOTE: TRACE
        print(f"DEBUG_LOS_PROB @{self.env.now:.2f}s: Elev={elevation_angle:.2f} -> Idx={idx} -> Prob={RURAL_LOS_PROB:.1f}%")
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
        
        # # NOTE: TRACE
        # print(f"DEBUG_SD_CL    @{self.env.now:.2f}s: Elev={elevation_angle:.2f} -> LoS_Shadow={los_shadowing:.2f} dB, NLoS_Total={nlos_shadowing_and_clutter:.2f} dB")
        
        return los_shadowing, nlos_shadowing_and_clutter

    def _freespacePL(self, freq_hz, dist_m):
        """ MATLAB의 freespacePL 함수를 변환. """
        if dist_m == 0:
            return 0
        # MATLAB 공식: 20*log10(f) + 20*log10(d) + 20*log10(4*pi/c)
        # 단위를 Hz, m로 사용
        fspl = 20 * math.log10(freq_hz) + 20 * math.log10(dist_m) + 20 * math.log10(4 * math.pi / LIGHT_SPEED)
        
        # debug:
        log10_freq_20 = 20 * math.log10(freq_hz)
        log10_dist_20 = 20 * math.log10(dist_m)
        log10_4pi_20 = 20 * math.log10(4 * math.pi / LIGHT_SPEED)
        
        # # NOTE: TRACE
        # print(f"DEBUG_FSPL     @{self.env.now:.2f}s: Dist={dist_m:.2f} m -> FSPL={fspl:.2f} dB")
        # print(f"freq_hz: {freq_hz}, dist_m: {dist_m:2f}")
        # print(f"log10_freq_20: @{log10_freq_20:.2f}, log10_dist_20: @{log10_dist_20:.2f}, log10_4pi_20: @{log10_4pi_20:.2f}")
        
        return fspl
    
    def _calculate_basic_path_loss(self, geo_info):
        """
        MATLAB의 GET_LOSS 로직을 구현. 
        LoS/NLoS 확률을 적용하여 최종 경로 손실을 계산합니다.
        """
        dist_m = geo_info['distance']
        elev_angle = geo_info['elevation_angle']
        freq_hz = SC9_CARRIER_FREQUENCY_HZ

        # 1. FSPL(자유 공간 경로 손실) 계산
        fspl = self._freespacePL(freq_hz, dist_m)
        
        # 2. LoS 확률 계산
        los_probability = self._los_prob(elev_angle)
        
        # 3. 섀도잉 및 클러터 손실 계산
        los_loss_component, nlos_loss_component = self._sd_cl(elev_angle)
        
        # 4. LoS/NLoS 확률을 가중치로 하여 최종 손실 계산
        # Loss = (Prob_LoS/100 * (FSPL + LoS_loss)) + ((100-Prob_LoS)/100 * (FSPL + NLoS_loss))
        basic_path_loss = (los_probability / 100) * (fspl + los_loss_component) + \
                     ((100 - los_probability) / 100) * (fspl + nlos_loss_component)
        
        # # NOTE: TRACE
        # print(f"DEBUG_BASIC_PATH_LOSS @{self.env.now:.2f}s: BASIC_PATH_LOSS={basic_path_loss:.2f} dB (FSPL={fspl:.2f}, ProbLoS={los_probability:.1f}%, sd_cl(los/nlos)={los_loss_component, nlos_loss_component})")
        return {
            "basic_path_loss": basic_path_loss,
            "fspl": fspl,
            "los_prob": los_probability,
            "los_shadowing": los_loss_component,
            "nlos_total_loss": nlos_loss_component
        }
    
    def _calculate_antenna_gain(self, antenna_angle_deg):
        # 0도일 경우, z=0이 되어 0으로 나누는 오류가 발생하므로 예외 처리
        if antenna_angle_deg == 0:
            return SC9_SATELLITE_TXGAIN

        # 파라미터 설정
        freq_hz = SC9_CARRIER_FREQUENCY_HZ
        aperture_radius_m = SC9_SATELLITE_ANTENNA_APERTURE

        ka = 2 * math.pi * freq_hz / LIGHT_SPEED * aperture_radius_m / 2
        z = ka * math.sin(math.radians(antenna_angle_deg))
        J = jv(1, z)
        normalized_gain_linear = 4 * (abs(J / z))**2
        gain_dbi = 10 * math.log10(normalized_gain_linear) + SC9_SATELLITE_TXGAIN
        
        return gain_dbi
    
    def calculate_rsrp(self, geo_info):
        # 1. 전체 경로 손실 계산 (기존과 동일)
        path_loss_details = self._calculate_basic_path_loss(geo_info)
        
        # 2. RB당 송신 파워 계산 (MATLAB 로직 반영)
        # 전체 Tx 파워를 RB 개수로 나눔 (dB 스케일에서는 빼기)
        tx_power_per_rb_dbm = SC9_SATELLITE_TXPW_dBm - 10 * math.log10(NUM_RESOURCE_BLOCKS)
        
        # 3. Antenna Gain 계산
        sat_tx_gain_dbi = self._calculate_antenna_gain(geo_info['antenna_angle'])
        
        # 4. 최종 RSRP 계산 (MATLAB 로직 반영)
        # RSRP = (RB당 파워) + (모든 안테나 이득) - (경로 손실) - (RS Factor)
        # TODO: Satellite의 Tx Antenna Gain은 추가 함수 구현 후 반영이 필요한 상태
        rsrp_dbm = tx_power_per_rb_dbm + sat_tx_gain_dbi + SC9_HANDHELD_RXGAIN - path_loss_details['basic_path_loss'] - 10 * math.log10(REFERENCE_SIGNAL_FACTOR)
        
        path_loss_details['rsrp'] = rsrp_dbm
        path_loss_details['tx_power_total_dbm'] = SC9_SATELLITE_TXPW_dBm
        path_loss_details['tx_power_per_rb_dbm'] = tx_power_per_rb_dbm
        path_loss_details['sat_tx_gain_dbi'] = sat_tx_gain_dbi
        path_loss_details['ue_rx_gain_dbi'] = SC9_HANDHELD_RXGAIN
        path_loss_details['rs_factor'] = REFERENCE_SIGNAL_FACTOR
        
        # # NOTE: TRACE
        # print(f"DEBUG @{self.env.now:.2f}s: RSRP: {rsrp_dbm:.2f} (TXPW={SC9_SATELLITE_TXPW_dBm} dBm, TXPW/RB={tx_power_per_rb_dbm:.2f} dBm, SAT_TXGAIN={sat_tx_gain_dbi:.2f} dBi, HANDHELD_RXGAIN={SC9_HANDHELD_RXGAIN} dBi, PATH_LOSS={path_loss:.2f} dB, REFERENCE_SIGNAL_FACTOR={REFERENCE_SIGNAL_FACTOR})")
        
        return path_loss_details

        # SC9_HANDHELD_NOISE_FIGURE

    def _calculate_sinr(self, signal_rsrp_dbm, interference_rsrp_list_dbm):
        # 1. 신호와 간섭을 선형 스케일(mW)로 변환
        signal_mw = 10**(signal_rsrp_dbm / 10)
        total_interference_mw = sum([10**(rsrp / 10) for rsrp in interference_rsrp_list_dbm])

        # 2. [핵심 수정] 잡음 전력을 표준 공식에 따라 동적으로 계산
        noise_dbm = THERMAL_NOISE_DENSITY + 10 * math.log10(SC9_RB_BANDWIDTH_HZ) + SC9_HANDHELD_NOISE_FIGURE
        noise_mw = 10**(noise_dbm / 10)

        # 3. SINR 계산
        sinr_linear = signal_mw / (total_interference_mw + noise_mw)
        sinr_db = 10 * math.log10(sinr_linear)
        
        # 4. [수정] SINR과 함께 계산된 Noise 값도 반환
        return sinr_db, noise_dbm
    
    # ==================== Geometry Calculation Functions ======================
    def get_geometry_info(self, satellite):
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


'''    
#----------------------------------------------#
#                                              # 
#         T  O    D  O    L  I  S  T           #
#                                              #
#----------------------------------------------#

<TODO: Measurement>
1) 거리, 고각, 안각 계산 (주기별, 캐싱 저장) - DONE
2) 안테나 패턴 구현, 채널 모델 구현 (RSRP) - DONE
3) 필더링 위성 외 Interference용 주변 위성 스캔 확장 - DONE
4) SINR/SNR 구현: 위성 필터링 범위, 계산 범위 >> Noise는 수정이 필요할 것으로 보임 - DONE
4-1) MR을 발송 시 현 상황 로그 출력 + 발송한 MR 정보 출력을 구현해서 확인, Satellite.py에서도 수용하도록 조정 - DONE
4-2) covered_by, outside에 대한 메소드를 신호 측정으로 대체, 핸드오버가 2차 발생하지 않는 것을 해결한 것으로 확인됨 - DONE
4-3) TTT 구현, SINR에 대한 기록 그래프 만드는 메소드 추가
5) K-filter 구현

<TODO: protocol>
1) INACTIVE 시 INITIAL ACCESS
2) RACH 구체적 구현
3) ~

'''