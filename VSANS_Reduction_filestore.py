import numpy as np
import h5py
import scipy as sp
from scipy.optimize.minpack import curve_fit
import matplotlib.pyplot as plt
from pathlib import Path
import dateutil
import datetime
from numpy.linalg import inv
from uncertainties import unumpy
import os

'''
This program is set to reduce VSANS data using middle and front detectors - fullpol available. Unpol and halfpol to follow shortly!
To do: Caluclate detector shadowing (masking); Add all available blocked beams and transmissions; Matrix Error Propagation; Add High Res Detector Option

Note about Masks (which are very important):
Must be in form #####_VSANS_TRANS_MASK.h5, #####_VSANS_SOLENOID_MASK.h5, or #####_VSANS_NOSOLENOID_MASK.h5, where ##### is the assocated filenumber and
the data with that filenumber must be in the data folder (used to match configurations). These masks can be made using IGOR.
'''

path = './'
TransPanel = 'MR' #Default is 'MR'
SectorCutAngles = 10.0 #Default is 10.0 (degrees)
UsePolCorr = 1 #Default is 1 to pol-ccorrect full-pol data, 0 means no
PlotYesNo = 1 #Default is 1 where 1 means yes, 0 means no
Absolute_Q_min = 0.005 #Default 0; Will take the maximum of Q_min_Calc from all detectors and this value
Absolute_Q_max = 0.145 #Default 0.6; Will take the minimum of Q_max_Calc from all detectors and this value
YesNo_2DFilesPerDetector = 0 #Default is 0 (no), 1 = yes; Note all detectors will be summed after beamline masking applied and can be read by SasView 4.2.2 (and higher?)

Excluded_Filenumbers = [] #Default is [] 56647, 56648
ReAssignBlockBeam = [] #Default is []
ReAssignEmpty = [] #Default is []

YesNoManualHe3Entry = 0 #0 for no (default), 1 for yes; should not be needed for data taken after July 2019 if He3 cells are properly registered
New_HE3_Files = [28422, 28498, 28577, 28673, 28755, 28869] #Default is []; These would be the starting files for each new cell IF YesNoManualHe3Entry = 1
MuValues = [3.105, 3.374, 3.105, 3.374, 3.105, 3.374] #Default is []; Values only used IF YesNoManualHe3Entry = 1; example [3.374, 3.105]=[Fras, Bur]; should not be needed after July 2019
TeValues = [0.86, 0.86, 0.86, 0.86, 0.86, 0.86] #Default is []; Values only used IF YesNoManualHe3Entry = 1; example [0.86, 0.86]=[Fras, Bur]; should not be needed after July 2019

#*************************************************
#***        Definitions, Functions             ***
#*************************************************

short_detectors = ["MT", "MB", "MR", "ML", "FT", "FB", "FR", "FL"]
middle_detectors = ["MT", "MB", "MR", "ML"]
FILE_STORE = {}

def LoadVSANSFiles():
    filelist = [fn for fn in os.listdir(path) if fn.endswith(".nxs.ngv") and not fn.startswith("PLEX")]
    filelist.sort()
    filestore = {}
    for filename in filelist:
        try:
            f = h5py.File(filename)
        except Exception:
            continue
        filenumber = f.get('entry/DAS_logs/trajectoryData/instFileNum', [None])[0]
        if filenumber is None:
            continue
        filestore[filenumber] = f
    
    return filestore


def Unique_Config_ID(filenumber):
    f = FILE_STORE[filenumber]
    
    Desired_FrontCarriage_Distance = int(f['entry/DAS_logs/carriage1Trans/desiredSoftPosition'][0]) #in cm
    Desired_MiddleCarriage_Distance = int(f['entry/DAS_logs/carriage2Trans/desiredSoftPosition'][0]) #in cm
    Wavelength = f['entry/DAS_logs/wavelength/wavelength'][0]
    GuideHolder = f['entry/DAS_logs/guide/guide'][0]
    if b"CONV" in GuideHolder:
        Guides =  "CvB"
    else:
        GuideNum = int(f['entry/DAS_logs/guide/guide'][0])
        Guides = "{GuideNum:d}".format(GuideNum=GuideNum) + "Gd"
    '''
    GuideHolder = f['entry/DAS_logs/guide/guide'][0]
    if str(GuideHolder).find("CONV") != -1:
        Guides =  int(0)
    else:
        Guides = int(f['entry/DAS_logs/guide/guide'][0])
    '''
    Configuration_ID = "{Guides}{Desired_FrontCarriage_Distance:d}cmF{Desired_MiddleCarriage_Distance:d}cmM{Wavelength:.f4}Ang".format(**locals())
        
    return Configuration_ID

def File_Type(filenumber):
    f = FILE_STORE[filenumber]
    Type = 'UNKNOWN'
    SolenoidPosition = 'UNKNOWN'
    
    Purpose = f['entry/reduction/file_purpose'][0]
    '''Purpose means SCATT, TRANS, HE3'''
    if b"SCATT" in Purpose:
        Type = 'SCATT'
    else:
        Type = 'TRANS'
    # you could also write: Type = 'SCATT' if b'SCATT' in Purpose else 'TRANS'
    if "backPolarization" in f['entry/DAS_logs/']:
        BackPolDirection = f['entry/DAS_logs/backPolarization/direction'][()]
    else:
        BackPolDirection = [b'UNPOLARIZED']
    if "UP" in str(BackPolDirection) or "DOWN" in str(BackPolDirection):
        SolenoidPosition = 'IN'
    else:
        SolenoidPosition = 'OUT'

    return Type, SolenoidPosition

def SortDataAutomatic(YesNoManualHe3Entry, New_HE3_Files, MuValues, TeValues):

    BlockBeam = {}
    Configs = {}
    Sample_Names = {}
    Scatt = {}
    Trans = {}
    Pol_Trans = {}
    HE3_Trans = {}
    FileNumberList = [0]

    UU_filenumber = -10
    DU_filenumber = -10
    DD_filenumber = -10
    UD_filenumber = -10
    filenames = '0'
    record_adam4021 = 0
    record_temp = 0
    CellIdentifier = 0
    HE3OUT_filenumber = -10
    start_number = 0
    
    filenumbers = sorted(list(FILE_STORE.keys()))
    # remove excluded:
    filtered_filenumbers = [fn for fn in filenumbers if not fn in Excluded_Filenumbers]
    for filenumber in filtered_filenumbers:
        if start_number == 0:
            start_number = filenumber
        f = FILE_STORE[filenumber]
        Count_time = f['entry/collection_time'][0]
        Descrip = str(f['entry/sample/description'][0])
        Descrip = Descrip[2:]
        Descrip = Descrip[:-1]
        if Count_time > 59 and "Align" in str(Descrip):
            FileNumberList.append(filenumber)
            print('Reading:', filenumber, ' ', Descrip)
            Listed_Config = str(f['entry/DAS_logs/configuration/key'][0])
            Listed_Config = Listed_Config[2:]
            Listed_Config = Listed_Config[:-1]
            Sample_Name = Descrip.replace(Listed_Config, '')
            Not_Sample = ['T_UU', 'T_DU', 'T_DD', 'T_UD', 'T_SM', 'T_NP', 'HeIN', 'HeOUT', 'S_UU', 'S_DU', 'S_DD', 'S_UD', 'S_NP', 'S_HeU', 'S_HeD', 'S_SMU', 'S_SMD']
            for i in Not_Sample:
                Sample_Name = Sample_Name.replace(i, '')
            Desired_Temp = 'na'
            if "temp" in f['entry/DAS_logs/']:
                Desired_Temp = str(f['entry/DAS_logs/temp/desiredPrimaryNode'][(0)])
                record_temp = 1    
            Voltage = 'na'
            if "adam4021" in f['entry/DAS_logs/']:
                Voltage = str(f['entry/DAS_logs/adam4021/voltage'][(0)])
                record_adam4021 = 1
            DT5 = Desired_Temp + " K,"
            DT4 = Desired_Temp + " K"
            DT3 = Desired_Temp + "K,"
            DT2 = Desired_Temp + "K"
            DT1 = Desired_Temp
            V5 = Voltage + " V,"
            V4 = Voltage + " V"
            V3 = Voltage + "V,"
            V2 = Voltage + "V"
            V1 = Voltage
            Not_Sample = [DT5, DT4, DT3, DT2, DT1, V5, V4, V3, V2, V1]
            for i in Not_Sample:
                Sample_Name = Sample_Name.replace(i, '')
            Sample_Name = Sample_Name.replace(' ', '')
            Sample_Base = Sample_Name
            Sample_Name = Sample_Name + '_' + str(Voltage) + 'V_' + str(Desired_Temp) + 'K'

            Purpose = f['entry/reduction/file_purpose'][0] #SCATT, TRANS, HE3
            Intent = f['entry/reduction/intent'][0] #Sample, Empty, Blocked Beam, Open Beam
            if filenumber in ReAssignBlockBeam:
                Intent = 'Blocked Beam'
            if filenumber in ReAssignEmpty:
                Intent = 'Empty'
            Type = str(f['entry/sample/description'][()])
            End_time = dateutil.parser.parse(f['entry/end_time'][0])
            TimeOfMeasurement = (End_time.timestamp() - Count_time/2)/3600.0 #in hours
            Trans_Counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
            '''
            #ID = str(f['entry/sample/group_id'][0])
            #trans_mask = Trans_masks['MR']
            #trans_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
            #trans_data = trans_data*trans_mask
            #Trans_Counts = trans_data.sum()
            '''
            MonCounts = f['entry/control/monitor_counts'][0]
            Trans_Distance = f['entry/instrument/detector_{ds}/distance'.format(ds=TransPanel)][0]
            Attenuation = f['entry/DAS_logs/attenuator/attenuator'][0]
            Wavelength = f['entry/DAS_logs/wavelength/wavelength'][0]
            Config = Unique_Config_ID(filenumber)
            if "frontPolarization" in f['entry/DAS_logs/']:
                FrontPolDirection = f['entry/DAS_logs/frontPolarization/direction'][()]
            else:
                FrontPolDirection = [b'UNPOLARIZED']
            if "backPolarization" in f['entry/DAS_logs/']:
                BackPolDirection = f['entry/DAS_logs/backPolarization/direction'][()]
            else:
                BackPolDirection = [b'UNPOLARIZED']

            '''Want to populate Config representative filenumbers on scattering filenumber'''
            config_filenumber = 0
            if "SCATT" in str(Purpose):
                config_filenumber = filenumber
            if Config not in Configs:
                Configs[Config] = config_filenumber
            if Configs[Config] == 0 and config_filenumber != 0:
                Configs[Config] = config_filenumber
            if Config not in BlockBeam:
                BlockBeam[Config] = {'Scatt':{'File' : 'NA'}, 'Trans':{'File' : 'NA', 'CountsPerSecond' : 'NA'}}
            ''' 
            if len(Configs) < 1:
                Configs = {Config : config_filenumber}
            else:
                if Config not in Configs:
                    Configs.append({Config : config_filenumber})
            if Configs[Config] == 0 and config_filenumber != 0:
                Configs[Config] = config_filenumber
            '''
                
            if "Blocked" in str(Intent): #i.e. a blocked beam
                if Config not in BlockBeam:
                        BlockBeam[Config] = {'Scatt':{'File' : 'NA'}, 'Trans':{'File' : 'NA', 'CountsPerSecond' : 'NA'}}
                        
                if str(Purpose).find("TRANS") != -1 or str(Purpose).find("HE3") != -1:
                    if 'NA' in BlockBeam[Config]['Trans']['File']:
                        BlockBeam[Config]['Trans']['File'] = [filenumber]
                        BlockBeam[Config]['Trans']['CountsPerSecond'] = [Trans_Counts/Count_time]
                    else:
                        BlockBeam[Config]['Trans']['File'].append(filenumber)
                        BlockBeam[Config]['Trans']['CountsPerSecond'].append(Trans_Counts/Count_time)
                        
                elif str(Purpose).find("SCATT") != -1:
                    if 'NA' in BlockBeam[Config]['Scatt']['File']:
                        BlockBeam[Config]['Scatt']['File'] = [filenumber]
                    else:
                        BlockBeam[Config]['Scatt']['File'].append(filenumber)
                    
            elif str(Intent).find("Sample") != -1 or str(Intent).find("Empty") != -1 or str(Intent).find("Open") != -1:
                if len(Sample_Names) < 1:
                    Sample_Names = [Sample_Name]
                else:
                    if Sample_Name not in Sample_Names:
                        Sample_Names.append(Sample_Name)
                        
                Intent_short = str(Intent)
                Intent_short = Intent_short[3:-2]
                Intent_short = Intent_short.replace(' Cell', '')
                Intent_short = Intent_short.replace(' Beam', '')
                    

                
                if str(Purpose).find("SCATT") != -1:
                    if Sample_Name not in Scatt:
                        Scatt[Sample_Name] = {'Intent': Intent_short, 'Sample_Base': Sample_Base, 'Config(s)' : {Config : {'Unpol': 'NA', 'U' : 'NA', 'D' : 'NA','UU' : 'NA', 'DU' : 'NA', 'DD' : 'NA', 'UD' : 'NA', 'UU_Time' : 'NA', 'DU_Time' : 'NA', 'DD_Time' : 'NA', 'UD_Time' : 'NA'}}}
                    if Config not in Scatt[Sample_Name]['Config(s)']:
                        Scatt[Sample_Name]['Config(s)'][Config] = {'Unpol': 'NA', 'U' : 'NA', 'D' : 'NA','UU' : 'NA', 'DU' : 'NA', 'DD' : 'NA', 'UD' : 'NA', 'UU_Time' : 'NA', 'DU_Time' : 'NA', 'DD_Time' : 'NA', 'UD_Time' : 'NA'}
                    if str(FrontPolDirection).find("UNPOLARIZED") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['Unpol']:
                            Scatt[Sample_Name]['Config(s)'][Config]['Unpol'] = [filenumber]
                        else:
                            Scatt[Sample_Name]['Config(s)'][Config]['Unpol'].append(filenumber)
                    if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['U']:
                            Scatt[Sample_Name]['Config(s)'][Config]['U'] = [filenumber]
                        else:
                            Scatt[Sample_Name]['Config(s)'][Config]['U'].append(filenumber)
                    if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['D']:
                            Scatt[Sample_Name]['Config(s)'][Config]['D'] = [filenumber]
                        else:
                            Scatt[Sample_Name]['Config(s)'][Config]['D'].append(filenumber)
                            
                    if YesNoManualHe3Entry != 1:        
                        if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UP") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['UU']:
                                Scatt[Sample_Name]['Config(s)'][Config]['UU'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['UU_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['UU'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['UU_Time'].append(TimeOfMeasurement)
                                
                        if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UP") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['DU']:
                                Scatt[Sample_Name]['Config(s)'][Config]['DU'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['DU_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['DU'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['DU_Time'].append(TimeOfMeasurement)

                        if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['DD']:
                                Scatt[Sample_Name]['Config(s)'][Config]['DD'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['DD_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['DD'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['DD_Time'].append(TimeOfMeasurement)
                                
                        if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['UD']:
                                Scatt[Sample_Name]['Config(s)'][Config]['UD'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['UD_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['UD'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['UD_Time'].append(TimeOfMeasurement)
                    else:
                        if Type[-6:-2] == 'S_UU': #str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UP") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['UU']:
                                Scatt[Sample_Name]['Config(s)'][Config]['UU'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['UU_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['UU'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['UU_Time'].append(TimeOfMeasurement)
                                
                        if Type[-6:-2] == 'S_DU': #str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UP") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['DU']:
                                Scatt[Sample_Name]['Config(s)'][Config]['DU'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['DU_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['DU'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['DU_Time'].append(TimeOfMeasurement)

                        if Type[-6:-2] == 'S_DD': #str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['DD']:
                                Scatt[Sample_Name]['Config(s)'][Config]['DD'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['DD_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['DD'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['DD_Time'].append(TimeOfMeasurement)
                                
                        if Type[-6:-2] == 'S_UD': #str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            if 'NA' in Scatt[Sample_Name]['Config(s)'][Config]['UD']:
                                Scatt[Sample_Name]['Config(s)'][Config]['UD'] = [filenumber]
                                Scatt[Sample_Name]['Config(s)'][Config]['UD_Time'] = [TimeOfMeasurement]
                            else:
                                Scatt[Sample_Name]['Config(s)'][Config]['UD'].append(filenumber)
                                Scatt[Sample_Name]['Config(s)'][Config]['UD_Time'].append(TimeOfMeasurement)
                
                        
                    
                if str(Purpose).find("TRANS") != -1:
                    if Sample_Name not in Trans:
                        Trans[Sample_Name] = {'Intent': Intent_short, 'Sample_Base': Sample_Base, 'Config(s)' : {Config : {'Unpol_Files': 'NA', 'U_Files' : 'NA', 'D_Files' : 'NA','Unpol_Trans_Cts': 'NA', 'U_Trans_Cts' : 'NA', 'D_Trans_Cts' : 'NA'}}}
                    if Config not in Trans[Sample_Name]['Config(s)']:
                        Trans[Sample_Name]['Config(s)'][Config] = {'Unpol_Files': 'NA', 'U_Files' : 'NA', 'D_Files': 'NA','Unpol_Trans_Cts': 'NA', 'U_Trans_Cts' : 'NA', 'D_Trans_Cts' : 'NA'}
                    if Sample_Name not in Pol_Trans:
                        Pol_Trans[Sample_Name] = {'T_UU' : {'File' : 'NA'},
                                                    'T_DU' : {'File' : 'NA'},
                                                    'T_DD' : {'File' : 'NA'},
                                                    'T_UD' : {'File' : 'NA'},
                                                    'T_SM' : {'File' : 'NA'},
                                                    'Config' : 'NA'
                                                    }
                    if str(FrontPolDirection).find("UNPOLARIZED") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files']:
                            Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files'] = [filenumber]
                        else:
                            Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files'].append(filenumber)
                    if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Trans[Sample_Name]['Config(s)'][Config]['U_Files']:
                            Trans[Sample_Name]['Config(s)'][Config]['U_Files'] = [filenumber]
                        else:
                            Trans[Sample_Name]['Config(s)'][Config]['U_Files'].append(filenumber)
                    if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                        if 'NA' in Trans[Sample_Name]['Config(s)'][Config]['D_Files']:
                            Trans[Sample_Name]['Config(s)'][Config]['D_Files'] = [filenumber]
                        else:
                            Trans[Sample_Name]['Config(s)'][Config]['D_Files'].append(filenumber)
                    if YesNoManualHe3Entry != 1:
                        if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UP") != -1:
                            UU_filenumber = filenumber
                            UU_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UP") != -1:
                            DU_filenumber = filenumber
                            DU_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            DD_filenumber = filenumber
                            DD_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            UD_filenumber = filenumber
                            UD_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                            SM_filenumber = filenumber
                            if SM_filenumber - UU_filenumber == 4:
                                if 'NA' in Pol_Trans[Sample_Name]['T_UU']['File']:
                                    Pol_Trans[Sample_Name]['T_UU']['File'] = [UU_filenumber]
                                    Pol_Trans[Sample_Name]['T_UU']['Meas_Time'] = [UU_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_UU']['File'].append(UU_filenumber)
                                    Pol_Trans[Sample_Name]['T_UU']['Meas_Time'].append(UU_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_DU']['File']:
                                    Pol_Trans[Sample_Name]['T_DU']['File'] = [DU_filenumber]
                                    Pol_Trans[Sample_Name]['T_DU']['Meas_Time'] = [DU_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_DU']['File'].append(DU_filenumber)
                                    Pol_Trans[Sample_Name]['T_DU']['Meas_Time'].append(DU_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_DD']['File']:
                                    Pol_Trans[Sample_Name]['T_DD']['File'] = [DD_filenumber]
                                    Pol_Trans[Sample_Name]['T_DD']['Meas_Time'] = [DD_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_DD']['File'].append(DD_filenumber)
                                    Pol_Trans[Sample_Name]['T_DD']['Meas_Time'].append(DD_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_UD']['File']:
                                    Pol_Trans[Sample_Name]['T_UD']['File'] = [UD_filenumber]
                                    Pol_Trans[Sample_Name]['T_UD']['Meas_Time'] = [UD_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_UD']['File'].append(UD_filenumber)
                                    Pol_Trans[Sample_Name]['T_UD']['Meas_Time'].append(UD_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_SM']['File']:
                                    Pol_Trans[Sample_Name]['T_SM']['File'] = [SM_filenumber]
                                else:
                                    Pol_Trans[Sample_Name]['T_SM']['File'].append(SM_filenumber)
                                if 'NA' in Pol_Trans[Sample_Name]['Config']:
                                    Pol_Trans[Sample_Name]['Config'] = [Config]
                                else:
                                    Pol_Trans[Sample_Name]['Config'].append(Config)
                    else:
                        if Type[-6:-2] == 'T_UU': #str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UP") != -1:
                            UU_filenumber = filenumber
                            UU_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if Type[-6:-2] == 'T_DU': #str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("UP") != -1:
                            DU_filenumber = filenumber
                            DU_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if Type[-6:-2] == 'T_DD': #str(FrontPolDirection).find("DOWN") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            DD_filenumber = filenumber
                            DD_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if Type[-6:-2] == 'T_UD': #str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("DOWN") != -1:
                            UD_filenumber = filenumber
                            UD_Time = (End_time.timestamp() - Count_time/2)/3600.0
                        if Type[-6:-2] == 'T_SM': #str(FrontPolDirection).find("UP") != -1 and str(BackPolDirection).find("UNPOLARIZED") != -1:
                            SM_filenumber = filenumber
                            if SM_filenumber - UU_filenumber == 4:
                                if 'NA' in Pol_Trans[Sample_Name]['T_UU']['File']:
                                    Pol_Trans[Sample_Name]['T_UU']['File'] = [UU_filenumber]
                                    Pol_Trans[Sample_Name]['T_UU']['Meas_Time'] = [UU_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_UU']['File'].append(UU_filenumber)
                                    Pol_Trans[Sample_Name]['T_UU']['Meas_Time'].append(UU_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_DU']['File']:
                                    Pol_Trans[Sample_Name]['T_DU']['File'] = [DU_filenumber]
                                    Pol_Trans[Sample_Name]['T_DU']['Meas_Time'] = [DU_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_DU']['File'].append(DU_filenumber)
                                    Pol_Trans[Sample_Name]['T_DU']['Meas_Time'].append(DU_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_DD']['File']:
                                    Pol_Trans[Sample_Name]['T_DD']['File'] = [DD_filenumber]
                                    Pol_Trans[Sample_Name]['T_DD']['Meas_Time'] = [DD_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_DD']['File'].append(DD_filenumber)
                                    Pol_Trans[Sample_Name]['T_DD']['Meas_Time'].append(DD_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_UD']['File']:
                                    Pol_Trans[Sample_Name]['T_UD']['File'] = [UD_filenumber]
                                    Pol_Trans[Sample_Name]['T_UD']['Meas_Time'] = [UD_Time]
                                else:
                                    Pol_Trans[Sample_Name]['T_UD']['File'].append(UD_filenumber)
                                    Pol_Trans[Sample_Name]['T_UD']['Meas_Time'].append(UD_Time)
                                if 'NA' in Pol_Trans[Sample_Name]['T_SM']['File']:
                                    Pol_Trans[Sample_Name]['T_SM']['File'] = [SM_filenumber]
                                else:
                                    Pol_Trans[Sample_Name]['T_SM']['File'].append(SM_filenumber)
                                if 'NA' in Pol_Trans[Sample_Name]['Config']:
                                    Pol_Trans[Sample_Name]['Config'] = [Config]
                                else:
                                    Pol_Trans[Sample_Name]['Config'].append(Config)

                    
                if str(Purpose).find("HE3") != -1:
                    
                    HE3Type = str(f['entry/sample/description'][()])
                    if HE3Type[-7:-2] == 'HeOUT':
                        if Sample_Name not in Trans:
                            Trans[Sample_Name] = {'Intent': Intent_short, 'Sample_Base': Sample_Base, 'Config(s)' : {Config : {'Unpol_Files': 'NA', 'U_Files' : 'NA', 'D_Files' : 'NA','Unpol_Trans_Cts': 'NA', 'U_Trans_Cts' : 'NA', 'D_Trans_Cts' : 'NA'}}}
                        if Config not in Trans[Sample_Name]['Config(s)']:
                            Trans[Sample_Name]['Config(s)'][Config] = {'Unpol_Files': 'NA', 'U_Files' : 'NA', 'D_Files': 'NA','Unpol_Trans_Cts': 'NA', 'U_Trans_Cts' : 'NA', 'D_Trans_Cts' : 'NA'}
                        if 'NA' in Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files']:
                            Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files'] = [filenumber]
                        else:
                            Trans[Sample_Name]['Config(s)'][Config]['Unpol_Files'].append(filenumber)
                    
                    if YesNoManualHe3Entry == 1:
                        if filenumber in New_HE3_Files:
                            ScaledOpacity = MuValues[CellIdentifier]
                            TE = TeValues[CellIdentifier]
                            CellTimeIdentifier = (End_time.timestamp() - Count_time)/3600.0
                            HE3Insert_Time = (End_time.timestamp() - Count_time)/3600.0
                            CellIdentifier += 1    
                    else: #i.e. automatic entry
                        CellTimeIdentifier = f['/entry/DAS_logs/backPolarization/timestamp'][0]/3600000 #milliseconds to hours
                        CellName = str(f['entry/DAS_logs/backPolarization/name'][0])
                        CellName = CellName[2:]
                        CellName = CellName[:-1]
                        CellName = CellName + str(CellTimeIdentifier)
                        CellTimeIdentifier = f['/entry/DAS_logs/backPolarization/timestamp'][0]/3600000 #milliseconds to hours
                        if CellTimeIdentifier not in HE3_Trans:
                            HE3Insert_Time = f['/entry/DAS_logs/backPolarization/timestamp'][0]/3600000 #milliseconds to hours
                            Opacity = f['/entry/DAS_logs/backPolarization/opacityAt1Ang'][0]
                            Wavelength = f['/entry/DAS_logs/wavelength/wavelength'][0]
                            ScaledOpacity = Opacity*Wavelength
                            TE = f['/entry/DAS_logs/backPolarization/glassTransmission'][0]
                    if HE3Type[-7:-2] == 'HeOUT':
                        HE3OUT_filenumber = filenumber
                        HE3OUT_config = Config
                        HE3OUT_sample = Sample_Name
                        HE3OUT_attenuators = int(f['entry/instrument/attenuator/num_atten_dropped'][0])
                    elif HE3Type[-7:-2] == ' HeIN':
                        HE3IN_filenumber = filenumber
                        HE3IN_config = Config
                        HE3IN_sample = Sample_Name
                        HE3IN_attenuators = int(f['entry/instrument/attenuator/num_atten_dropped'][0])
                        HE3IN_StartTime = (End_time.timestamp() - Count_time/2)/3600.0
                        if HE3OUT_filenumber > 0:
                            if HE3OUT_config == HE3IN_config and HE3OUT_attenuators == HE3IN_attenuators and HE3OUT_sample == HE3IN_sample: #This implies that you must have a 3He out before 3He in of same config and atten
                                if HE3Insert_Time not in HE3_Trans:
                                    HE3_Trans[CellTimeIdentifier] = {'Te' : TE,
                                                                    'Mu' : ScaledOpacity,
                                                                    'Insert_time' : HE3Insert_Time}
                                Elasped_time = HE3IN_StartTime - HE3Insert_Time
                                if "Elasped_time" not in HE3_Trans[CellTimeIdentifier]:
                                    HE3_Trans[CellTimeIdentifier]['Config'] = [HE3IN_config]
                                    HE3_Trans[CellTimeIdentifier]['HE3_OUT_file'] = [HE3OUT_filenumber]
                                    HE3_Trans[CellTimeIdentifier]['HE3_IN_file'] = [HE3IN_filenumber]
                                    HE3_Trans[CellTimeIdentifier]['Elasped_time'] = [Elasped_time]
                                    HE3_Trans[CellTimeIdentifier]['Cell_name'] = [CellName]
                                else:
                                    HE3_Trans[CellTimeIdentifier]['Config'].append(HE3IN_config)
                                    HE3_Trans[CellTimeIdentifier]['HE3_OUT_file'].append(HE3OUT_filenumber)
                                    HE3_Trans[CellTimeIdentifier]['HE3_IN_file'].append(HE3IN_filenumber)
                                    HE3_Trans[CellTimeIdentifier]['Elasped_time'].append(Elasped_time)
                                    HE3_Trans[CellTimeIdentifier]['Cell_name'].append(CellName)

    return Sample_Names, Configs, BlockBeam, Scatt, Trans, Pol_Trans, HE3_Trans, start_number, FileNumberList

def ReadIn_Masks(filenumberlisting):

    Masks = {}
    single_mask = {}

    filename = '0'
    Mask_files = [fn for fn in os.listdir("./") if fn.endswith("MASK.h5")]
    if len(Mask_files) >= 1:
        for name in Mask_files:
            filename = str(name)
            associated_filenumber = filename[:5]
            if associated_filenumber.isdigit() == True:
                if associated_filenumber not in str(filenumberlisting):
                    print('Need scan for filenumber ', associated_filenumber, ' to proces its associated mask.')
                else:    
                    ConfigID = Unique_Config_ID(associated_filenumber)
                    if ConfigID not in Masks:
                        Masks[ConfigID] = {'Trans' : 'NA', 'Scatt_Standard' : 'NA', 'Scatt_WithSolenoid' : 'NA'}
                    Type, SolenoidPosition = File_Type(associated_filenumber)
                    config = Path(filename)
                    if config.is_file():
                        f = h5py.File(filename)
                        for dshort in short_detectors:
                            mask_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                            '''
                            This reverses zeros and ones (assuming IGOR-made masks) so that zeros become the pixels to ignore:
                            '''
                            single_mask[dshort] = np.zeros_like(mask_data)
                            single_mask[dshort][mask_data == 0] = 1.0
                            
                        if str(Type).find("TRANS") != -1:
                            Masks[ConfigID]['Trans'] = single_mask.copy()
                            print('Saved', filename, ' as Trans Mask for', ConfigID)
                            
                        if str(Type).find("SCATT") != -1 and str(SolenoidPosition).find("OUT") != -1:
                            Masks[ConfigID]['Scatt_Standard'] = single_mask.copy()
                            print('Saved', filename, ' as Standard Scatt Mask for', ConfigID)
                            
                        if str(Type).find("SCATT") != -1 and str(SolenoidPosition).find("IN") != -1:
                            Masks[ConfigID]['Scatt_WithSolenoid'] = single_mask.copy()
                            print('Saved', filename, ' as Scatt Mask With Solenoid for', ConfigID)                     
                
    return Masks

def Process_Transmissions(BlockBeam, Masks, HE3_Trans, Pol_Trans, Trans):

    for Cell in HE3_Trans:
        if 'Elasped_time' in HE3_Trans[Cell]:
            counter = 0
            for InFile in HE3_Trans[Cell]['HE3_IN_file']:
                OutFile = HE3_Trans[Cell]['HE3_OUT_file'][counter]
                Config = HE3_Trans[Cell]['Config'][counter]
                if Config in BlockBeam:
                    if 'NA' not in BlockBeam[Config]['Trans']['File']:
                        BBFile = BlockBeam[Config]['Trans']['File'][0]
                    elif 'NA' not in BlockBeam[Config]['Scatt']['File']:
                        BBFile = BlockBeam[Config]['Scatt']['File'][0]
                    else:
                        BBFile = 0
                if Config in Masks and 'NA' not in Masks[Config]['Trans']:
                    mask_it = np.array(Masks[Config]['Trans'][TransPanel])
                    f = FILE_STORE[InFile]
                    g = FILE_STORE[OutFile]
                    INMon = f['entry/control/monitor_counts'][0]
                    IN_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    INCount_time = f['entry/collection_time'][0]
                    OUTMon = g['entry/control/monitor_counts'][0]
                    OUT_data = np.array(g['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    OUTCount_time = g['entry/collection_time'][0]
                    if BBFile == 0:
                        BB_data = np.zeros_like(IN_data)
                        BBCount_time = 1.0  
                    else:
                        h = FILE_STORE[BBFile]
                        BBCount_time = h['entry/collection_time'][0]
                        BB_data = np.array(h['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])   
                    trans_num = (IN_data - BB_data*(INCount_time/BBCount_time))*mask_it
                    trans_denom = (OUT_data - BB_data*(OUTCount_time/BBCount_time))*mask_it
                    trans = (np.sum(trans_num)/np.sum(trans_denom))*(OUTMon / INMon)
                    if 'Transmission' not in HE3_Trans[Cell]:
                        HE3_Trans[Cell]['Transmission'] = [trans]
                    else:
                        HE3_Trans[Cell]['Transmission'].append(trans)
                else: #no mask for HE3_Trans
                    f = FILE_STORE[InFile]
                    INMon = f['entry/control/monitor_counts'][0]
                    IN_counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    INCount_time = f['entry/collection_time'][0]
                    g = FILE_STORE[OutFile]
                    OUTMon = g['entry/control/monitor_counts'][0]
                    OUT_counts = g['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    OUTCount_time = g['entry/collection_time'][0]
                    if BBFile == 0:
                        BB_counts = 0.0
                        BBCount_time = 1.0
                    else:
                        h = FILE_STORE[BBFile]
                        BBCount_time = h['entry/collection_time'][0]
                        BB_counts = h['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    trans_num = (IN_counts - BB_counts*(INCount_time/BBCount_time))
                    trans_denom = (OUT_counts - BB_counts*(OUTCount_time/BBCount_time))
                    trans = (trans_num/trans_denom)*(OUTMon / INMon)
                    if 'Transmission' not in HE3_Trans[Cell]:
                        HE3_Trans[Cell]['Transmission'] = [trans]
                    else:
                        HE3_Trans[Cell]['Transmission'].append(trans)                
                counter += 1

    for Samp in Pol_Trans:
        if 'NA' not in Pol_Trans[Samp]['T_UU']['File']:
            counter = 0
            for UUFile in Pol_Trans[Samp]['T_UU']['File']:
                DUFile = Pol_Trans[Samp]['T_DU']['File'][counter]
                DDFile = Pol_Trans[Samp]['T_DD']['File'][counter]
                UDFile = Pol_Trans[Samp]['T_UD']['File'][counter]
                SMFile = Pol_Trans[Samp]['T_SM']['File'][counter]
                Config = Pol_Trans[Samp]['Config'][counter]
                if Config in BlockBeam:
                    if 'NA' not in BlockBeam[Config]['Trans']['File']:
                        BBFile = BlockBeam[Config]['Trans']['File'][0]
                    elif 'NA' not in BlockBeam[Config]['Scatt']['File']:
                        BBFile = BlockBeam[Config]['Scatt']['File'][0]
                    else:
                        BBFile = 0
                if Config in Masks and 'NA' not in Masks[Config]['Trans']:
                    mask_it = np.array(Masks[Config]['Trans'][TransPanel])
                    f = FILE_STORE[UUFile]
                    g = FILE_STORE[DUFile]
                    h = FILE_STORE[DDFile]
                    j = FILE_STORE[UDFile]
                    k = FILE_STORE[SMFile]
                    UUMon = f['entry/control/monitor_counts'][0]
                    UU_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    UUCount_time = f['entry/collection_time'][0]
                    DUMon = g['entry/control/monitor_counts'][0]
                    DU_data = np.array(g['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    DUCount_time = g['entry/collection_time'][0]
                    DDMon = h['entry/control/monitor_counts'][0]
                    DD_data = np.array(h['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    DDCount_time = h['entry/collection_time'][0]
                    UDMon = j['entry/control/monitor_counts'][0]
                    UD_data = np.array(j['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    UDCount_time = j['entry/collection_time'][0]
                    SMMon = k['entry/control/monitor_counts'][0]
                    SM_data = np.array(k['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    SMCount_time = k['entry/collection_time'][0]
                    if BBFile == 0:
                        BB_data = np.zeros_like(UU_data)
                        BBCount_time = 1.0
                    else:
                        l = FILE_STORE[BBFile]
                        BBMon = l['entry/control/monitor_counts'][0]
                        BBCount_time = l['entry/collection_time'][0]
                        BB_data = np.array(l['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    trans_UU = (UU_data - BB_data*(UUCount_time/BBCount_time))*mask_it
                    trans_DU = (DU_data - BB_data*(DUCount_time/BBCount_time))*mask_it
                    trans_DD = (DD_data - BB_data*(DDCount_time/BBCount_time))*mask_it
                    trans_UD = (UD_data - BB_data*(UDCount_time/BBCount_time))*mask_it
                    trans_SM = (SM_data - BB_data*(SMCount_time/SMCount_time))*mask_it
                    UU_value = (np.sum(trans_UU)/np.sum(trans_SM))*(SMMon / UUMon)
                    UD_value = (np.sum(trans_UD)/np.sum(trans_SM))*(SMMon / UDMon)
                    DD_value = (np.sum(trans_DD)/np.sum(trans_SM))*(SMMon / DDMon)
                    DU_value = (np.sum(trans_DU)/np.sum(trans_SM))*(SMMon / DUMon)
                    SM_value = np.sum(trans_SM*1E8/SMMon)
                    if 'Trans' not in Pol_Trans[Samp]['T_UU']:
                        Pol_Trans[Samp]['T_UU']['Trans'] = [UU_value]
                        Pol_Trans[Samp]['T_DU']['Trans'] = [DU_value]
                        Pol_Trans[Samp]['T_DD']['Trans'] = [DD_value]
                        Pol_Trans[Samp]['T_UD']['Trans'] = [UD_value]
                        Pol_Trans[Samp]['T_SM']['Trans_Cts'] = [SM_value]
                    else:
                        Pol_Trans[Samp]['T_UU']['Trans'].append(UU_value)
                        Pol_Trans[Samp]['T_DU']['Trans'].append(DU_value)
                        Pol_Trans[Samp]['T_DD']['Trans'].append(DD_value)
                        Pol_Trans[Samp]['T_UD']['Trans'].append(UD_value)
                        Pol_Trans[Samp]['T_SM']['Trans_Cts'].append(SM_value)
                else:
                    f = FILE_STORE[UUFile]
                    g = FILE_STORE[DUFile]
                    h = FILE_STORE[DDFile]
                    j = FILE_STORE[UDFile]
                    k = FILE_STORE[SMFile]
                    UUMon = f['entry/control/monitor_counts'][0]
                    UU_counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    UUCount_time = f['entry/collection_time'][0]
                    DUMon = g['entry/control/monitor_counts'][0]
                    DU_counts = g['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    DUCount_time = g['entry/collection_time'][0]
                    DDMon = h['entry/control/monitor_counts'][0]
                    DD_counts = h['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    DDCount_time = h['entry/collection_time'][0]
                    UDMon = j['entry/control/monitor_counts'][0]
                    UD_counts = j['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    UDCount_time = j['entry/collection_time'][0]
                    SMMon = k['entry/control/monitor_counts'][0]
                    SM_counts = k['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    SMCount_time = k['entry/collection_time'][0]
                    if BBFile == 0:
                        BB_counts = 0.0
                        BBCount_time = 1.0
                    else:
                        l = FILE_STORE[BBFile]
                        BBCount_time = l['entry/collection_time'][0]
                        BB_counts = l['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    trans_UU = (UU_counts - BB_counts*(UUCount_time/BBCount_time))
                    trans_DU = (DU_counts - BB_counts*(DUCount_time/BBCount_time))
                    trans_DD = (DD_counts - BB_counts*(DDCount_time/BBCount_time))
                    trans_UD = (UD_counts - BB_counts*(UDCount_time/BBCount_time))
                    trans_SM = (SM_counts - BB_counts*(SMCount_time/BBCount_time))
                    UU_value = (trans_UU/trans_SM)*(SMMon / UUMon)
                    UD_value = (trans_DU/trans_SM)*(SMMon / UDMon)
                    DD_value = (trans_DD/trans_SM)*(SMMon / DDMon)
                    DU_value = (trans_UD/trans_SM)*(SMMon / DUMon)
                    SM_value = trans_SM*1E8/SMMon
                    if 'Trans' not in Pol_Trans[Samp]['T_UU']:
                        Pol_Trans[Samp]['T_UU']['Trans'] = [UU_value]
                        Pol_Trans[Samp]['T_DU']['Trans'] = [DU_value]
                        Pol_Trans[Samp]['T_DD']['Trans'] = [DD_value]
                        Pol_Trans[Samp]['T_UD']['Trans'] = [UD_value]
                        Pol_Trans[Samp]['T_SM']['Trans_Cts'] = [SM_value]
                    else:
                        Pol_Trans[Samp]['T_UU']['Trans'].append(UU_value)
                        Pol_Trans[Samp]['T_DU']['Trans'].append(DU_value)
                        Pol_Trans[Samp]['T_DD']['Trans'].append(DD_value)
                        Pol_Trans[Samp]['T_UD']['Trans'].append(UD_value)
                        Pol_Trans[Samp]['T_SM']['Trans_Cts'].append(SM_value)
                counter += 1

    for Samp in Trans:
        for Config in Trans[Samp]['Config(s)']:
            if Config in BlockBeam:
                if 'NA' not in BlockBeam[Config]['Trans']['File']:
                    BBFile = BlockBeam[Config]['Trans']['File'][0]
                elif 'NA' not in BlockBeam[Config]['Scatt']['File']:
                    BBFile = BlockBeam[Config]['Scatt']['File'][0]
                else:
                    BBFile = 0
                if Config in Masks and 'NA' not in Masks[Config]['Trans']:
                    mask_it = np.array(Masks[Config]['Trans'][TransPanel])
                    if BBFile == 0:
                        example_file = Configs[Config]
                        l = FILE_STORE[example_file]
                        Example_data = np.array(l['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                        BB_data = np.zeros_like(Example_data)
                        BBCount_time = 1.0
                    else:
                        BB = path + "sans" + str(BBFile) + ".nxs.ngv"
                        l = FILE_STORE[BBFile]
                        BBMon = l['entry/control/monitor_counts'][0]
                        BBCount_time = l['entry/collection_time'][0]
                        BB_data = np.array(l['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                    if 'NA' not in Trans[Samp]['Config(s)'][Config]['Unpol_Files']:
                        for UNF in Trans[Samp]['Config(s)'][Config]['Unpol_Files']:
                            f = FILE_STORE[UNF]
                            UNMon = f['entry/control/monitor_counts'][0]
                            UN_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                            UNCount_time = f['entry/collection_time'][0]
                            UNTrans = (UN_data - BB_data*(UNCount_time/BBCount_time))*mask_it
                            UN_Trans = np.sum(UNTrans)*1E8/UNMon
                            if 'NA' in Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts']:
                                Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts'] = [UN_Trans]
                            else:
                                Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts'].append(UN_Trans)        
                    if 'NA' not in Trans[Samp]['Config(s)'][Config]['U_Files']:
                        for UF in Trans[Samp]['Config(s)'][Config]['U_Files']:
                            f = FILE_STORE[UF]
                            UMon = f['entry/control/monitor_counts'][0]
                            U_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
                            UCount_time = f['entry/collection_time'][0]
                            UTrans = (U_data - BB_data*(UCount_time/BBCount_time))*mask_it
                            U_Trans = np.sum(UTrans)*1E8/UMon
                            if 'NA' in Trans[Samp]['Config(s)'][Config]['U_Trans_Cts']:
                                Trans[Samp]['Config(s)'][Config]['U_Trans_Cts'] = [U_Trans]
                            else:
                                Trans[Samp]['Config(s)'][Config]['U_Trans_Cts'].append(U_Trans)
                else:
                    if BBFile == 0:
                        BB_counts = 0.0
                        BBCount_time = 1.0
                    else:
                        BB = path + "sans" + str(BBFile) + ".nxs.ngv"
                        l = FILE_STORE[BBFile]
                        BBCount_time = l['entry/collection_time'][0]
                        BB_counts = l['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                    if 'NA' not in Trans[Samp]['Config(s)'][Config]['Unpol_Files']:
                        for UNF in Trans[Samp]['Config(s)'][Config]['Unpol_Files']:
                            UN_file = path + "sans" + str(UNF) + ".nxs.ngv"
                            f = FILE_STORE[UNF]
                            UNMon = f['entry/control/monitor_counts'][0]
                            UN_counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                            UNCount_time = f['entry/collection_time'][0]
                            UNTrans = (UN_counts - BB_counts*(UNCount_time/BBCount_time))
                            UN_Trans = UNTrans * 1E8 / UNMon
                            if 'NA' in Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts']:
                                Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts'] = [UN_Trans]
                            else:
                                Trans[Samp]['Config(s)'][Config]['Unpol_Trans_Cts'].append(UN_Trans)
                    if 'NA' not in Trans[Samp]['Config(s)'][Config]['U_Files']:
                        for UF in Trans[Samp]['Config(s)'][Config]['U_Files']:
                            f = FILE_STORE[UF]
                            UMon = f['entry/control/monitor_counts'][0]
                            U_counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]
                            UCount_time = f['entry/collection_time'][0]
                            UTrans = (U_counts - BB_counts*(UCount_time/BBCount_time))
                            U_Trans = UTrans * 1E8 / UMon
                            if 'NA' in Trans[Samp]['Config(s)'][Config]['U_Trans_Cts']:
                                Trans[Samp]['Config(s)'][Config]['U_Trans_Cts'] = [U_Trans]
                            else:
                                Trans[Samp]['Config(s)'][Config]['U_Trans_Cts'].append(U_Trans)
                                         
    return

def Process_ScattFiles():

    for Sample_Name in Scatt:
        if str(Scatt[Sample_Name]['Intent']).find("Empty") != -1:
            for CF in Scatt[Sample_Name]['Config(s)']:
                if 'NA' in Scatt[Sample_Name]['Config(s)'][CF]['DD'] and 'NA' not in Scatt[Sample_Name]['Config(s)'][CF]['UU']:
                    Scatt[Sample_Name]['Config(s)'][CF]['DD'] = Scatt[Sample_Name]['Config(s)'][CF]['UU']
                    Scatt[Sample_Name]['Config(s)'][CF]['DD_Time'] = Scatt[Sample_Name]['Config(s)'][CF]['UU_Time']
                elif 'NA' in Scatt[Sample_Name]['Config(s)'][CF]['UU'] and 'NA' not in Scatt[Sample_Name]['Config(s)'][CF]['DD']:
                    Scatt[Sample_Name]['Config(s)'][CF]['UU'] = Scatt[Sample_Name]['Config(s)'][CF]['DD']
                    Scatt[Sample_Name]['Config(s)'][CF]['UU_Time'] = Scatt[Sample_Name]['Config(s)'][CF]['DD_Time']
                if 'NA' in Scatt[Sample_Name]['Config(s)'][CF]['UD'] and 'NA' not in Scatt[Sample_Name]['Config(s)'][CF]['DU']:
                    Scatt[Sample_Name]['Config(s)'][CF]['UD'] = Scatt[Sample_Name]['Config(s)'][CF]['DU']
                    Scatt[Sample_Name]['Config(s)'][CF]['UD_Time'] = Scatt[Sample_Name]['Config(s)'][CF]['DU_Time']
                elif 'NA' in Scatt[Sample_Name]['Config(s)'][CF]['DU'] and 'NA' not in Scatt[Sample_Name]['Config(s)'][CF]['UD']:
                    Scatt[Sample_Name]['Config(s)'][CF]['DU'] = Scatt[Sample_Name]['Config(s)'][CF]['UD']
                    Scatt[Sample_Name]['Config(s)'][CF]['DU_Time'] = Scatt[Sample_Name]['Config(s)'][CF]['UD_Time']
                    
    return


def Plex_File(start_number):

    PlexData = {}

    filename = '0'
    Plex_file = [fn for fn in os.listdir(path) if fn.startswith("PLEX")]
    try:
        filename = Plex_file[0]
        filepath = os.path.join(path, filename)
        print('Reading in ', filepath)
        f = h5py.File(filepath)
        for dshort in short_detectors:
            data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
            PlexData[dshort] = data #.flatten()
    except Exception:
        # pick the first file from the FILE_STORE:
        f = list(FILE_STORE.values())[0]
        for dshort in short_detectors:
            data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
            data_zeros = np.ones_like(data)
            PlexData[dshort] = data_zeros #.flatten()
        print('Plex file not found; populated with ones instead')   
            
    return PlexData

def BlockedBeamScattCountsPerSecond(Config, representative_filenumber):

    BB_per_second = {}
    print('BlockBeams(s) for', Config, ':')

    if Config in BlockBeam:
        if 'NA' not in BlockBeam[Config]['Trans']['File']:
            BBFile = BlockBeam[Config]['Trans']['File'][0]
            f = FILE_STORE[BBFile]
            Count_time = f['entry/collection_time'][0]
            for dshort in short_detectors:
                bb_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                BB_per_second[dshort] = bb_data / Count_time
            print('Trans BB', BBFile)
        if 'NA' not in BlockBeam[Config]['Scatt']['File']:
            BBFile = BlockBeam[Config]['Scatt']['File'][0]
            f = FILE_STORE[BBFile]
            Count_time = f['entry/collection_time'][0]
            for dshort in short_detectors:
                bb_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                BB_per_second[dshort] = bb_data / Count_time
            print('Scatt BB', BBFile)
        if 'NA' in BlockBeam[Config]['Trans']['File'] and 'NA' in BlockBeam[Config]['Scatt']['File']:
            f = FILE_STORE[representative_filenumber]
            for dshort in short_detectors:
                bb_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                zero_data = np.zeros_like(bb_data)
                BB_per_second[dshort] = zero_data
            print('No BB')
    else:
        f = FILE_STORE[representative_filenumber]
        for dshort in short_detectors:
            bb_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
            zero_data = np.zeros_like(bb_data)
            BB_per_second[dshort] = zero_data
        print('BB set to be zero')    

    return BB_per_second

def BlockedBeam_Averaged(BlockedBeamFiles, MeasMasks, Trans_masks):

    BlockBeam_Trans = {}
    BlockBeam_ScattPerPixel = {}
    masks = {}

    for filenumber in BlockedBeamFiles:
        print('Reading in block beam file number:', filenumber)
        f = FILE_STORE[filenumber]
        Config_ID = Unique_Config_ID(filenumber)
        Purpose = f['entry/reduction/file_purpose'][()]
        Count_time = f['entry/collection_time'][0]
        if str(Purpose).find("TRANS") != -1 or str(Purpose).find("HE3") != -1: 
            '''#Trans_Counts = f['entry/instrument/detector_{ds}/integrated_count'.format(ds=TransPanel)][0]'''  
            trans_mask = Trans_masks['MR']
            trans_data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=TransPanel)])
            trans_data = trans_data*trans_mask
            Trans_Counts = trans_data.sum()
            
            BlockBeam_Trans[Config_ID] = {'File' : filenumber,
                                                            'CountsPerSecond' : Trans_Counts/Count_time}
        if str(Purpose).find("TRANS") != -1 or str(Purpose).find("HE3") != -1:
            print('BB scattering number is ', filenumber)
            BlockBeam_ScattPerPixel[Config_ID] = {'File' : filenumber}
            for dshort in short_detectors:
                Holder = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                if Config_ID in MeasMasks:
                    masks = MeasMasks[Config_ID]
                else:
                    masks[dshort] = np.ones_like(Holder)
                Sum = np.sum(Holder[masks[dshort] > 0])
                Pixels = np.sum(masks[dshort])
                Unc = np.sqrt(Sum)/Pixels
                Ave = np.average(Holder[masks[dshort] > 0])
                
                BlockBeam_ScattPerPixel[Config_ID][dshort] = {'AvePerSec' : Ave/Count_time, 'Unc' : Unc/Count_time}
                
    return BlockBeam_Trans, BlockBeam_ScattPerPixel

def SolidAngle_AllDetectors(representative_filenumber):
    Solid_Angle = {}
    f = FILE_STORE[representative_filenumber]
    for dshort in short_detectors:
        detector_distance = f['entry/instrument/detector_{ds}/distance'.format(ds=dshort)][0]
        x_pixel_size = f['entry/instrument/detector_{ds}/x_pixel_size'.format(ds=dshort)][0]/10.0
        y_pixel_size = f['entry/instrument/detector_{ds}/y_pixel_size'.format(ds=dshort)][0]/10.0
        if dshort == 'MT' or dshort == 'MB' or dshort == 'FT' or dshort == 'FB':
            setback = f['entry/instrument/detector_{ds}/setback'.format(ds=dshort)][0]
        else:
            setback = 0
            
        realDistZ = detector_distance + setback
        theta_x_step = x_pixel_size / realDistZ
        theta_y_step = y_pixel_size / realDistZ
        Solid_Angle[dshort] = theta_x_step * theta_y_step

    return Solid_Angle

def QCalculation_AllDetectors(representative_filenumber):

    Q_total = {}
    deltaQ = {}
    Qx = {}
    Qy = {}
    Qz = {}
    Q_perp_unc = {}
    Q_parl_unc = {}
    InPlaneAngleMap = {}
    dimXX = {}
    dimYY = {}

    f = FILE_STORE[representative_filenumber]
    for dshort in short_detectors:
        data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
        Wavelength = f['entry/instrument/beam/monochromator/wavelength'][0]
        Wavelength_spread = f['entry/instrument/beam/monochromator/wavelength_spread'][0]
        dimX = f['entry/instrument/detector_{ds}/pixel_num_x'.format(ds=dshort)][0]
        dimY = f['entry/instrument/detector_{ds}/pixel_num_y'.format(ds=dshort)][0]
        dimXX[dshort] = f['entry/instrument/detector_{ds}/pixel_num_x'.format(ds=dshort)][0]
        dimYY[dshort] = f['entry/instrument/detector_{ds}/pixel_num_y'.format(ds=dshort)][0]
        beam_center_x = f['entry/instrument/detector_{ds}/beam_center_x'.format(ds=dshort)][0]
        beam_center_y = f['entry/instrument/detector_{ds}/beam_center_y'.format(ds=dshort)][0]
        beamstop_diameter = f['/entry/DAS_logs/C2BeamStop/diameter'][0]/10.0 #beam stop in cm; sits right in front of middle detector?
        detector_distance = f['entry/instrument/detector_{ds}/distance'.format(ds=dshort)][0]
        x_pixel_size = f['entry/instrument/detector_{ds}/x_pixel_size'.format(ds=dshort)][0]/10.0
        y_pixel_size = f['entry/instrument/detector_{ds}/y_pixel_size'.format(ds=dshort)][0]/10.0
        panel_gap = f['entry/instrument/detector_{ds}/panel_gap'.format(ds=dshort)][0]/10.0
        coeffs = f['entry/instrument/detector_{ds}/spatial_calibration'.format(ds=dshort)][0][0]/10.0
        SampleApInternal = f['/entry/DAS_logs/geometry/internalSampleApertureHeight'][0] #internal sample aperture in cm
        SampleApExternal = f['/entry/DAS_logs/geometry/externalSampleApertureHeight'][0] #external sample aperture in cm
        SourceAp = f['/entry/DAS_logs/geometry/sourceApertureHeight'][0] #source aperture in cm, assumes circular aperture(?) #0.75, 1.5, or 3 for guides; otherwise 6 cm for >= 1 guides
        FrontDetToGateValve = f['/entry/DAS_logs/carriage/frontTrans'][0] #400
        MiddleDetToGateValve = f['/entry/DAS_logs/carriage/middleTrans'][0] #1650
        FrontDetToSample = f['/entry/DAS_logs/geometry/sampleToFrontLeftDetector'][0] #491.4
        MiddleDetToSample = f['/entry/DAS_logs/geometry/sampleToMiddleLeftDetector'][0] #1741.4
        SampleToSourceAp = f['/entry/DAS_logs/geometry/sourceApertureToSample'][0] #1490.6; "Calculated distance between sample and source aperture" in cm
        '''
        #GateValveToSample = f['/entry/DAS_logs/geometry/samplePositionOffset'][0] #e.g. 91.4; gate valve to sample in cm ("Hand-measured distance from the center of the table the sample is mounted on to the sample. A positive value means the sample is offset towards the guides.")
        #SampleToSampleAp = f['/entry/DAS_logs/geometry/SampleApertureOffset'][0] #e.g. 106.9; sample to sample aperture in cm ("Hand-measured distance between the Sample aperture and the sample.")            
        #SampleApToSourceAp = f['/entry/DAS_logs/geometry/sourceApertureToSampleAperture'][0] #1383.7; "Calculated distance between sample aperture and source aperture" in cm
        #Note gate valve to source aperture distances are based on the number of guides used:
        #0=2441; 1=2157; 2=1976; 3=1782; 4=1582; 5=1381; 6=1181; 7=980; 8=780; 9=579 in form of # guides=distance in cm
        '''
            
        if dshort == 'MT' or dshort == 'MB' or dshort == 'FT' or dshort == 'FB':
            setback = f['entry/instrument/detector_{ds}/setback'.format(ds=dshort)][0]
            vertical_offset = f['entry/instrument/detector_{ds}/vertical_offset'.format(ds=dshort)][0]
            lateral_offset = 0
        else:
            setback = 0
            vertical_offset = 0
            lateral_offset = f['entry/instrument/detector_{ds}/lateral_offset'.format(ds=dshort)][0]

        realDistZ = detector_distance + setback

        position_key = dshort[1]
        if position_key == 'T':
            realDistX =  coeffs
            realDistY =  0.5 * y_pixel_size + vertical_offset + panel_gap/2.0
        elif position_key == 'B':
            realDistX =  coeffs
            realDistY =  vertical_offset - (dimY - 0.5)*y_pixel_size - panel_gap/2.0
        elif position_key == 'L':
            realDistX =  lateral_offset - (dimX - 0.5)*x_pixel_size - panel_gap/2.0
            realDistY =  coeffs
        elif position_key == 'R':
            realDistX =  x_pixel_size*(0.5) + lateral_offset + panel_gap/2.0
            realDistY =  coeffs

        X, Y = np.indices(data.shape)
        x0_pos =  realDistX - beam_center_x + (X)*x_pixel_size 
        y0_pos =  realDistY - beam_center_y + (Y)*y_pixel_size
        InPlane0_pos = np.sqrt(x0_pos**2 + y0_pos**2)
        twotheta = np.arctan2(InPlane0_pos,realDistZ)
        phi = np.arctan2(y0_pos,x0_pos)
        '''#Q resolution from J. of Appl. Cryst. 44, 1127-1129 (2011) and file:///C:/Users/kkrycka/Downloads/SANS_2D_Resolution.pdf where
        #there seems to be an extra factor of wavelength listed that shouldn't be there in (delta_wavelength/wavelength):'''
        carriage_key = dshort[0]
        if carriage_key == 'F':
            L2 = FrontDetToSample
        elif carriage_key == 'M':
            L2 = MiddleDetToSample
        L1 = SampleToSourceAp
        Pix = 0.82
        R1 = SourceAp #source aperture radius in cm
        R2 = SampleApExternal #sample aperture radius in cm
        Inv_LPrime = 1.0/L1 + 1.0/L2
        k = 2*np.pi/Wavelength
        Sigma_D_Perp = np.sin(phi)*x_pixel_size + np.cos(phi)*y_pixel_size
        Sigma_D_Parl = np.cos(phi)*x_pixel_size + np.sin(phi)*y_pixel_size
        SigmaQPerpSqr = (k*k/12.0)*(3*np.power(R1/L1,2) + 3.0*np.power(R2*Inv_LPrime,2)+ np.power(Sigma_D_Perp/L2,2))
        SigmaQParlSqr = (k*k/12.0)*(3*np.power(R1/L1,2) + 3.0*np.power(R2*Inv_LPrime,2)+ np.power(Sigma_D_Parl/L2,2))
        R = np.sqrt(np.power(x0_pos,2)+np.power(y0_pos,2))
        Q0 = k*R/L2
        '''
        #If no gravity correction:
        #SigmaQParlSqr = SigmaQParlSqr + np.power(Q0,2)*np.power(Wavelength_spread/np.sqrt(6.0),2)
        #Else, if adding gravity correction:
        '''
        g = 981 #in cm/s^2
        m_div_h = 252.77 #in s cm^-2
        A = -0.5*981*L2*(L1+L2)*np.power(m_div_h , 2)
        WL = Wavelength*1E-8
        SigmaQParlSqr = SigmaQParlSqr + np.power(Wavelength_spread*k/(L2),2)*(R*R -4*A*np.sin(phi)*WL*WL + 4*A*A*np.power(WL,4))/6.0 #gravity correction makes vary little difference for wavelength spread < 20%
        '''VSANS IGOR 2D ASCII delta_Q seems to be way off the mark, but this 2D calculaation matches the VSANS circular average closely when pixels are converted to circular average...'''
        
        Q_total[dshort] = (4.0*np.pi/Wavelength)*np.sin(twotheta/2.0)
        QQ_total = (4.0*np.pi/Wavelength)*np.sin(twotheta/2.0)
        Qx[dshort] = QQ_total*np.cos(twotheta/2.0)*np.cos(phi)
        Qy[dshort] = QQ_total*np.cos(twotheta/2.0)*np.sin(phi)
        Qz[dshort] = QQ_total*np.sin(twotheta/2.0)     
        Q_perp_unc[dshort] = np.ones_like(Q_total[dshort])*np.sqrt(SigmaQPerpSqr)
        Q_parl_unc[dshort] = np.sqrt(SigmaQParlSqr)
        Theta_deg = phi*180.0/np.pi
        InPlaneAngleMap[dshort] = Theta_deg
        '''#returns values between -180.0 degrees and +180.0 degrees'''
        
        #plt.imshow[LM.T, origin='lower']

    return Qx, Qy, Qz, Q_total, Q_perp_unc, Q_parl_unc, InPlaneAngleMap, dimXX, dimYY

def SectorMask_AllDetectors(InPlaneAngleMap, PrimaryAngle, AngleWidth, BothSides):

    SectorMask = {}

    for dshort in short_detectors:
        Angles = InPlaneAngleMap[dshort]
        SM = np.zeros_like(Angles)
        SM[np.absolute(Angles - PrimaryAngle) <= AngleWidth] = 1.0
        SM[np.absolute(Angles + 360 - PrimaryAngle) <= AngleWidth] = 1.0
        SM[np.absolute(Angles - 360 - PrimaryAngle) <= AngleWidth] = 1.0

        if BothSides > 0:
            SecondaryAngle = PrimaryAngle + 180
            if SecondaryAngle > 360:
                SecondaryAngle = SecondaryAngle - 360
        SM[np.absolute(Angles - SecondaryAngle) <= AngleWidth] = 1.0
        SM[np.absolute(Angles + 360 - SecondaryAngle) <= AngleWidth] = 1.0
        SM[np.absolute(Angles - 360 - SecondaryAngle) <= AngleWidth] = 1.0

        SectorMask[dshort] = SM

    return SectorMask
            
def He3Decay_func(t, p, gamma):
    return p * np.exp(-t / gamma)

def HE3_Pol_AtGivenTime(entry_time, HE3_Cell_Summary):
    '''
    #Predefine HE3_Cell_Summary[HE3_Trans[entry]['Insert_time']] = {'Atomic_P0' : P0, 'Gamma(hours)' : gamma, 'Mu' : Mu, 'Te' : Te}
    #He3Decay_func must be predefined
    '''

    counter = 0
    for time in HE3_Cell_Summary:
        if counter == 0:
            holder_time = time
            counter += 1
        if entry_time >= time:
            holder_time = time
        if entry_time < time:
            break
        
    delta_time = entry_time - holder_time     
    P0 = HE3_Cell_Summary[holder_time]['Atomic_P0']
    gamma = HE3_Cell_Summary[holder_time]['Gamma(hours)']
    Mu = HE3_Cell_Summary[holder_time]['Mu']
    Te = HE3_Cell_Summary[holder_time]['Te']
    AtomicPol = P0 * np.exp(-delta_time / gamma)
    NeutronPol = np.tanh(Mu * AtomicPol)
    UnpolHE3Trans = Te * np.exp(-Mu)*np.cosh(Mu * AtomicPol)
    T_MAJ = Te * np.exp(-Mu*(1.0 - AtomicPol))
    T_MIN = Te * np.exp(-Mu*(1.0 + AtomicPol))
        
    return NeutronPol, UnpolHE3Trans, T_MAJ, T_MIN

def HE3_DecayCurves(HE3_Trans):
    '''
    #Uses predefined He3Decay_func
    #Creates and returns HE3_Cell_Summary
    '''

    HE3_Cell_Summary = {}
    entry_number = 0
    
    for entry in HE3_Trans:
        entry_number += 1
        Mu = HE3_Trans[entry]['Mu']
        Te = HE3_Trans[entry]['Te']
        xdata = np.array(HE3_Trans[entry]['Elasped_time'])
        trans_data = np.array(HE3_Trans[entry]['Transmission'])
        ydata = np.arccosh(np.array(trans_data)/(np.e**(-Mu)*Te))/Mu

        if xdata.size < 2:
            P0 = ydata[0]
            gamma = 1000.0
            '''#assumes no appreciable time decay until more data obtained'''
            PCell0 = np.tanh(Mu * P0)
        else:
            popt, pcov = curve_fit(He3Decay_func, xdata, ydata)
            P0, gamma = popt
            PCell0 = np.tanh(Mu * P0)

        HE3_Cell_Summary[HE3_Trans[entry]['Insert_time']] = {'Atomic_P0' : P0, 'Gamma(hours)' : gamma, 'Mu' : Mu, 'Te' : Te}
        #print('He3Cell Summary for Cell Identity', entry, ':')
        print('He3Cell Summary for Cell Identity', HE3_Trans[entry]['Cell_name'][0], ':')
        print('PolCell0', PCell0, 'AtomicPol0: ', P0, ' Gamma: ', gamma)
        print('     ')

        if xdata.size >= 2:
            print('Graphing He3 decay curve....(close generated plot to continue)')
            fit = He3Decay_func(xdata, popt[0], popt[1])
            fig = plt.figure()
            plt.plot(xdata, ydata, 'b*', label='data')
            plt.plot(xdata, fit, 'r-', label='fit of data')
            plt.xlabel('time (hours)')
            plt.ylabel('3He atomic polarization')
            plt.title('He3 Cell Decay')
            plt.legend()
            fig.savefig('He3Curve_AtomicPolarization_Cell{cell}.png'.format(cell = entry))
            plt.show()

        if xdata.size >= 2 and entry_number == len(HE3_Trans):
            print('Graphing current and projected decay curves....(close generated plot to continue)')
            TMAJ_data = Te * np.exp(-Mu*(1.0 - ydata))
            TMIN_data = Te * np.exp(-Mu*(1.0 + ydata))
            xdatalonger = HE3_Trans[entry]['Elasped_time']
            L = len(xdata)
            last_time = xdata[L-1]
            for i in range(49):
                extra_time = last_time + i*1
                xdatalonger.append(extra_time)
            xdataextended = np.array(xdatalonger)
            AtomicPol_fitlonger = He3Decay_func(xdataextended, popt[0], popt[1])
            TMAJ_fit = Te * np.exp(-Mu*(1.0 - AtomicPol_fitlonger))
            TMIN_fit = Te * np.exp(-Mu*(1.0 + AtomicPol_fitlonger))
            
            fig = plt.figure()
            plt.plot(xdata, TMAJ_data, 'b*', label='T_MAJ data')
            plt.plot(xdataextended, TMAJ_fit, 'c-', label='T_MAJ predicted')

            plt.plot(xdata, TMIN_data, 'r*', label='T_MIN data')
            plt.plot(xdataextended, TMIN_fit, 'm-', label='T_MIN predicted')
            
            plt.xlabel('time (hours)')
            plt.ylabel('Spin Transmission')
            plt.title('Predicted He3 Cell Transmission')
            plt.legend()
            fig.savefig('PredictedHe3DecayCurve_{cell}.png'.format(cell = entry))
            plt.show()

        '''    
        #NeutronPol = np.tanh(Mu * AtomicPol)
        #UnpolHE3Trans = Te * np.exp(-Mu)*np.cosh(Mu * AtomicPol)
        #T_MAJ = Te * np.exp(-Mu*(1.0 - AtomicPol))
        #T_MIN = Te * np.exp(-Mu*(1.0 + AtomicPol))
        '''

    return HE3_Cell_Summary

def Pol_SuppermirrorAndFlipper(Pol_Trans, HE3_Cell_Summary):
    '''#Uses time of measurement from Pol_Trans,
    #saves PSM and PF values into Pol_Trans.
    #Uses prefefined HE3_Pol_AtGivenTime function.
    '''
    
    for ID in Pol_Trans:
        if 'Meas_Time' in Pol_Trans[ID]['T_UU']:
            for Time in Pol_Trans[ID]['T_UU']['Meas_Time']:
                NP, UT, T_MAJ, T_MIN = HE3_Pol_AtGivenTime(Time, HE3_Cell_Summary)
                if 'Neutron_Pol' not in Pol_Trans[ID]['T_UU']:
                    Pol_Trans[ID]['T_UU']['Neutron_Pol'] = [NP]
                    Pol_Trans[ID]['T_UU']['Unpol_Trans'] = [UT]
                else:
                    Pol_Trans[ID]['T_UU']['Neutron_Pol'].append(NP)
                    Pol_Trans[ID]['T_UU']['Unpol_Trans'].append(UT)
            for Time in Pol_Trans[ID]['T_DD']['Meas_Time']:
                NP, UT, T_MAJ, T_MIN = HE3_Pol_AtGivenTime(Time, HE3_Cell_Summary)
                if 'Neutron_Pol' not in Pol_Trans[ID]['T_DD']:
                    Pol_Trans[ID]['T_DD']['Neutron_Pol'] = [NP]
                    Pol_Trans[ID]['T_DD']['Unpol_Trans'] = [UT]
                else:
                    Pol_Trans[ID]['T_DD']['Neutron_Pol'].append(NP)
                    Pol_Trans[ID]['T_DD']['Unpol_Trans'].append(UT)       
            for Time in Pol_Trans[ID]['T_DU']['Meas_Time']:
                NP, UT, T_MAJ, T_MIN = HE3_Pol_AtGivenTime(Time, HE3_Cell_Summary)
                if 'Neutron_Pol' not in Pol_Trans[ID]['T_DU']:
                    Pol_Trans[ID]['T_DU']['Neutron_Pol'] = [NP]
                    Pol_Trans[ID]['T_DU']['Unpol_Trans'] = [UT]
                else:
                    Pol_Trans[ID]['T_DU']['Neutron_Pol'].append(NP)
                    Pol_Trans[ID]['T_DU']['Unpol_Trans'].append(UT)     
            for Time in Pol_Trans[ID]['T_UD']['Meas_Time']:
                NP, UT,T_MAJ, T_MIN = HE3_Pol_AtGivenTime(Time, HE3_Cell_Summary)
                if 'Neutron_Pol' not in Pol_Trans[ID]['T_UD']:
                    Pol_Trans[ID]['T_UD']['Neutron_Pol'] = [NP]
                    Pol_Trans[ID]['T_UD']['Unpol_Trans'] = [UT]
                else:
                    Pol_Trans[ID]['T_UD']['Neutron_Pol'].append(NP)
                    Pol_Trans[ID]['T_UD']['Unpol_Trans'].append(UT)
            

    for ID in Pol_Trans:
        if 'Neutron_Pol' in Pol_Trans[ID]['T_UU']:
            ABS = np.array(Pol_Trans[ID]['T_SM']['Trans_Cts'])
            Pol_Trans[ID]['AbsScale'] = np.average(ABS)

            UU = np.array(Pol_Trans[ID]['T_UU']['Trans'])
            UU_UnpolHe3Trans = np.array(Pol_Trans[ID]['T_UU']['Unpol_Trans'])
            UU_NeutronPol = np.array(Pol_Trans[ID]['T_UU']['Neutron_Pol'])
            DD = np.array(Pol_Trans[ID]['T_DD']['Trans'])
            DD_UnpolHe3Trans = np.array(Pol_Trans[ID]['T_DD']['Unpol_Trans'])
            DD_NeutronPol = np.array(Pol_Trans[ID]['T_DD']['Neutron_Pol'])
            UD = np.array(Pol_Trans[ID]['T_UD']['Trans'])
            UD_UnpolHe3Trans = np.array(Pol_Trans[ID]['T_UD']['Unpol_Trans'])
            UD_NeutronPol = np.array(Pol_Trans[ID]['T_UD']['Neutron_Pol'])
            DU = np.array(Pol_Trans[ID]['T_DU']['Trans'])
            DU_UnpolHe3Trans = np.array(Pol_Trans[ID]['T_DU']['Unpol_Trans'])
            DU_NeutronPol = np.array(Pol_Trans[ID]['T_DU']['Neutron_Pol'])
            print('  ')
            print(ID)
            print('UU_Cell', UU_NeutronPol, UU_UnpolHe3Trans)
            print('DU_Cell', DU_NeutronPol, DU_UnpolHe3Trans)
            print('DD_Cell', DD_NeutronPol, DD_UnpolHe3Trans)
            print('UD_Cell', UD_NeutronPol, UD_UnpolHe3Trans)

            PF = 1.00
            Pol_Trans[ID]['P_F'] = np.average(PF)
            PSMUU = (UU/UU_UnpolHe3Trans - 1.0)/(UU_NeutronPol)
            PSMDD = (DD/DD_UnpolHe3Trans - 1.0)/(DD_NeutronPol)
            PSMUD = (1.0 - UD/UD_UnpolHe3Trans)/(UD_NeutronPol)
            PSMDU = (1.0 - DU/DU_UnpolHe3Trans)/(DU_NeutronPol)
            PSM_Ave = 0.25*(np.average(PSMUU) + np.average(PSMDD) + np.average(PSMUD) + np.average(PSMDU))
            Pol_Trans[ID]['P_SM'] = np.average(PSM_Ave)
            print('PSM', Pol_Trans[ID]['P_SM'])
            

            if UsePolCorr == 0:
                '''#0 Means no, turn it off'''
                Pol_Trans[ID]['P_SM'] = 1.0
                Pol_Trans[ID]['P_F'] = 1.0
                print('Manually reset P_SM and P_F to unity')

    return

def AbsScale(ScattType, Sample, Config, BlockBeam_per_second, Solid_Angle, Plex):

    Scaled_Data = {}
    UncScaled_Data = {}
    masks = {}
    BB = {}

    if Sample in Scatt:
        if Config in Scatt[Sample]['Config(s)']:
            Number_Files = 1.0*len(Scatt[Sample]['Config(s)'][Config][ScattType])

            if ScattType == 'UU' or ScattType == 'DU'  or ScattType == 'DD'  or ScattType == 'UD' or ScattType == 'U' or ScattType == 'D':
                TransType = 'U_Trans_Cts'
                TransTypeAlt = 'Unpol_Trans_Cts'
            elif ScattType == 'Unpol':
                TransType = 'Unpol_Trans_Cts'
                TransTypeAlt = 'U_Trans_Cts'
            else:
                print('There is a problem with the Scatting Type requested in the Absobulte Scaling Function')
                
            ABS_Scale = 1.0
            if Sample in Trans and str(Scatt[Sample]['Config(s)'][Config][ScattType]).find('NA') == -1:
                if Sample in Trans:
                    if Config in Trans[Sample]['Config(s)']:
                        if TransType in Trans[Sample]['Config(s)'][Config]:
                            ABS_Scale = np.average(np.array(Trans[Sample]['Config(s)'][Config][TransType]))
                        elif TransTypeAlt in Trans[Sample]['Config(s)'][Config]:
                            ABS_Scale = np.average(np.array(Trans[Sample]['Config(s)'][Config][TransTypeAlt]))

                '''#Calculating an average block beam counts per pixel and time (seems to work better than a pixel-by-pixel subtraction,
                at least for shorter count times)'''
                
            for dshort in short_detectors:
                Holder =  np.array(BlockBeam_per_second[dshort])
                '''Optional:
                if Config in Masks:
                    if 'Scatt_WithSolenoidss' in Masks[Config]:   
                        masks[dshort] = Masks[Config]['Scatt_WithSolenoid'][dshort]
                    elif 'Scatt_Standardss' in Masks[Config]:
                        masks[dshort] = Masks[Config]['Scatt_Standard'][dshort]
                    else:
                        masks[dshort] = np.ones_like(Holder)
                else:
                    masks[dshort] = np.ones_like(Holder)
                '''
                masks[dshort] = np.ones_like(Holder)
                Sum = np.sum(Holder[masks[dshort] > 0])
                Pixels = np.sum(masks[dshort])
                Unc = np.sqrt(Sum)/Pixels
                Ave = np.average(Holder[masks[dshort] > 0])
                BB[dshort] = Ave

            filecounter = 0
            if str(Scatt[Sample]['Config(s)'][Config][ScattType]).find('NA') != -1:
                Scaled_Data = 'NA'
                UncScaled_Data = 'NA'
            else:
                for filenumber in Scatt[Sample]['Config(s)'][Config][ScattType]:
                    filecounter += 1
                    f = FILE_STORE[filenumber]
                    MonCounts = f['entry/control/monitor_counts'][0]
                    Count_time = f['entry/collection_time'][0]
                    for dshort in short_detectors:
                        data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                        unc = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
                        data = (data - Count_time*BB[dshort])/(Number_Files*Plex[dshort]*Solid_Angle[dshort])
                        if filecounter < 2:
                            Scaled_Data[dshort] = ((1E8/MonCounts)/ABS_Scale)*data
                            UncScaled_Data[dshort] = unc
                        else:
                            Scaled_Data[dshort] += ((1E8/MonCounts)/ABS_Scale)*data
                            UncScaled_Data[dshort] += unc           
                for dshort in short_detectors:
                    UncScaled_Data[dshort] = np.sqrt(UncScaled_Data[dshort])*((1E8/MonCounts)/ABS_Scale)/(Number_Files*Plex[dshort]*Solid_Angle[dshort])

        else:
            Scaled_Data = 'NA'
            UncScaled_Data = 'NA'
                
    return Scaled_Data, UncScaled_Data

def PolCorrScattFiles(dimXX, dimYY, Sample, Config, UUScaledData, DUScaledData, DDScaledData, UDScaledData, UUScaledData_Unc, DUScaledData_Unc, DDScaledData_Unc, UDScaledData_Unc):

    Scaled_Data = np.zeros((8,4,6144))
    UncScaled_Data = np.zeros((8,4,6144))

    Det_counter = 0
    for dshort in short_detectors:
        UUD = np.array(UUScaledData[dshort])
        Scaled_Data[Det_counter][0][:] += UUD.flatten()
        
        DUD = np.array(DUScaledData[dshort])
        Scaled_Data[Det_counter][1][:] += DUD.flatten()

        DDD = np.array(DDScaledData[dshort])
        Scaled_Data[Det_counter][2][:] += DDD.flatten()

        UDD = np.array(UDScaledData[dshort])
        Scaled_Data[Det_counter][3][:] += UDD.flatten()

        UUD_Unc = np.array(UUScaledData_Unc[dshort])
        UncScaled_Data[Det_counter][0][:] += UUD_Unc.flatten()
        
        DUD_Unc = np.array(DUScaledData_Unc[dshort])
        UncScaled_Data[Det_counter][1][:] += DUD_Unc.flatten()

        DDD_Unc = np.array(DDScaledData_Unc[dshort])
        UncScaled_Data[Det_counter][2][:] += DDD_Unc.flatten()

        UDD_Unc = np.array(UDScaledData_Unc[dshort])
        UncScaled_Data[Det_counter][3][:] += UDD_Unc.flatten()

        Det_counter += 1

    '''#Full-Pol Reduction:'''
    PolCorr_UU = {}
    PolCorr_DU = {}
    PolCorr_DD = {}
    PolCorr_UD = {}
    PolCorr_UU_Unc = {}
    PolCorr_DU_Unc = {}
    PolCorr_DD_Unc = {}
    PolCorr_UD_Unc = {}

    Pol_Efficiency = np.zeros((4,4))
    HE3_Efficiency = np.zeros((4,4))
    PolCorr_AllDetectors = {}
    HE3Corr_AllDetectors = {}
    Uncertainty_PolCorr_AllDetectors = {}
    Have_FullPol = 0
    if Sample in Trans and str(Scatt[Sample]['Config(s)'][Config]['UU']).find('NA') == -1 and str(Scatt[Sample]['Config(s)'][Config]['DU']).find('NA') == -1 and str(Scatt[Sample]['Config(s)'][Config]['DD']).find('NA') == -1 and str(Scatt[Sample]['Config(s)'][Config]['UD']).find('NA') == -1:
        Have_FullPol = 1

        if Sample in Pol_Trans:
            PSM = Pol_Trans[Sample]['P_SM']
            PF = Pol_Trans[Sample]['P_F']
            print(Sample, Config, 'PSM is', PSM)
        else:
            print(Sample, Config, 'missing P_F and P_SM; will proceed without pol-correction!')
            PF = 1.0
            PSM = 1.0
        '''#Calculating an average block beam counts per pixel and time (seems to work better than a pixel-by-pixel subtraction,
        at least for shorter count times)'''

        Number_UU = 1.0*len(Scatt[Sample]['Config(s)'][Config]["UU"])
        Number_DU = 1.0*len(Scatt[Sample]['Config(s)'][Config]["DU"])
        Number_DD = 1.0*len(Scatt[Sample]['Config(s)'][Config]["DD"])
        Number_UD = 1.0*len(Scatt[Sample]['Config(s)'][Config]["UD"])      
            
        Scatt_Type = ["UU", "DU", "DD", "UD"]
        for type in Scatt_Type:
            type_time = type + "_Time"
            filenumber_counter = 0
            for filenumber in Scatt[Sample]['Config(s)'][Config][type]:
                f = FILE_STORE[filenumber]
                entry = Scatt[Sample]['Config(s)'][Config][type_time][filenumber_counter]
                NP, UT, T_MAJ, T_MIN = HE3_Pol_AtGivenTime(entry, HE3_Cell_Summary)
                C = NP
                S = 0.9985
                '''#0.9985 is the highest I've recently gotten at 5.5 Ang from EuSe 60 nm 0.95 V and 2.0 K'''
                X = np.sqrt(PSM/S)
                if type == "UU":
                    CrossSection_Index = 0
                    UT = UT / Number_UU
                    Pol_Efficiency[CrossSection_Index][:] += [(C*(S*X*X + X) + S*X + 1)*UT, (C*(-S*X*X + X) - S*X + 1)*UT, (C*(S*X*X - X) - S*X + 1)*UT, (C*(-S*X*X - X) + S*X + 1)*UT]
                    HE3_Efficiency[CrossSection_Index][:] += [ UT, 0.0, 0.0, 0.0]
                elif type == "DU":
                    CrossSection_Index = 1
                    UT = UT / Number_DU
                    Pol_Efficiency[CrossSection_Index][:] += [(C*(-S*X*X + X) - S*X + 1)*UT, (C*(S*X*X + X) + S*X + 1)*UT, (C*(-S*X*X - X) + S*X + 1)*UT, (C*(S*X*X - X) - S*X + 1)*UT]
                    HE3_Efficiency[CrossSection_Index][:] += [ 0.0, UT, 0.0, 0.0]
                elif type == "DD":
                    CrossSection_Index = 2
                    UT = UT / Number_DD
                    Pol_Efficiency[CrossSection_Index][:] += [(C*(S*X*X - X) - S*X + 1)*UT, (C*(-S*X*X - X) + S*X + 1)*UT, (C*(S*X*X + X) + S*X + 1)*UT, (C*(-S*X*X + X) - S*X + 1)*UT]
                    HE3_Efficiency[CrossSection_Index][:] += [ 0.0, 0.0, UT, 0.0]
                elif type == "UD":
                    CrossSection_Index = 3
                    UT = UT / Number_UD
                    Pol_Efficiency[CrossSection_Index][:] += [(C*(-S*X*X - X) + S*X + 1)*UT, (C*(S*X*X - X) - S*X + 1)*UT, (C*(-S*X*X + X) - S*X + 1)*UT, (C*(S*X*X + X) + S*X + 1)*UT]
                    HE3_Efficiency[CrossSection_Index][:] += [ 0.0, 0.0, 0.0, UT]

        
        Prefactor = inv(Pol_Efficiency)
        PrefactorII = inv(HE3_Efficiency)
        
        Det_Index = 0
        for dshort in short_detectors:
            UncData_Per_Detector = UncScaled_Data[Det_Index][:][:]
            Data_Per_Detector = Scaled_Data[Det_Index][:][:]
            
            HE3Corr_Data = np.dot(PrefactorII, Data_Per_Detector)
            PolCorr_Data = np.dot(Prefactor, Data_Per_Detector)
            '''
            #Below is the code that allows true matrix error propagation, but it takes a while...so may want to optimize more before implementing.
            #Also will need to uncomment from uncertainties import unumpy (top).
            Data_Per_Detector2 = unumpy.umatrix(Scaled_Data[Det_Index][:][:], UncScaled_Data[Det_Index][:][:])
            PolCorr_Data2 = np.dot(Prefactor, Data_Per_Detector2)
            PolCorr_Data = unumpy.nominal_values(PolCorr_Data2)
            PolCorr_Unc = unumpy.std_devs(PolCorr_Data2)
            '''
            PolCorr_AllDetectors[dshort] = PolCorr_Data
            Uncertainty_PolCorr_AllDetectors[dshort] = UncData_Per_Detector
            HE3Corr_AllDetectors[dshort] = HE3Corr_Data
            Det_Index += 1

            dimX = dimXX[dshort]
            dimY = dimYY[dshort]
            PolCorr_UU[dshort] = PolCorr_AllDetectors[dshort][0][:][:].reshape((dimX, dimY))
            PolCorr_DU[dshort] = PolCorr_AllDetectors[dshort][1][:][:].reshape((dimX, dimY))
            PolCorr_DD[dshort] = PolCorr_AllDetectors[dshort][2][:][:].reshape((dimX, dimY))
            PolCorr_UD[dshort] = PolCorr_AllDetectors[dshort][3][:][:].reshape((dimX, dimY))

            PolCorr_UU_Unc[dshort] = Uncertainty_PolCorr_AllDetectors[dshort][0][:][:].reshape((dimX, dimY))
            PolCorr_DU_Unc[dshort] = Uncertainty_PolCorr_AllDetectors[dshort][1][:][:].reshape((dimX, dimY))
            PolCorr_DD_Unc[dshort] = Uncertainty_PolCorr_AllDetectors[dshort][2][:][:].reshape((dimX, dimY))
            PolCorr_UD_Unc[dshort] = Uncertainty_PolCorr_AllDetectors[dshort][3][:][:].reshape((dimX, dimY))

    return Have_FullPol, PolCorr_UU, PolCorr_DU, PolCorr_DD, PolCorr_UD, PolCorr_UU_Unc, PolCorr_DU_Unc, PolCorr_DD_Unc, PolCorr_UD_Unc

def MinMaxQ(Q_total):

    MinQ1 = np.amin(Q_total['MR'])
    MinQ2 = np.amin(Q_total['ML'])
    MinQ3 = np.amin(Q_total['MT'])
    MinQ4 = np.amin(Q_total['MB'])
    MinQs = np.array([MinQ1, MinQ2, MinQ3, MinQ4])
    MinQ_Middle = np.amin(MinQs)
    MaxQ1 = np.amax(Q_total['FR'])
    MaxQ2 = np.amax(Q_total['FL'])
    MaxQ3 = np.amax(Q_total['FT'])
    MaxQ4 = np.amax(Q_total['FB'])
    MaxQs = np.array([MaxQ1, MaxQ2, MaxQ3, MaxQ4])
    MaxQ_Front = np.amax(MaxQs)
    Q_min = MinQ_Middle 
    Q_max = MaxQ_Front

    return Q_min, Q_max

def TwoDimToOneDim(Key, Q_min, Q_max, Q_bins, QGridPerDetector, generalmask, sectormask, PolCorr_AllDetectors, Unc_PolCorr_AllDetectors, ID, Config, PlotYesNo):

    masks = {}
    for dshort in short_detectors:
        masks[dshort] = generalmask[dshort]*sectormask[dshort]
    
    Q_Values = np.linspace(Q_min, Q_max, Q_bins, endpoint=True)
    Q_step = (Q_max - Q_min) / Q_bins
    
    FrontUU = np.zeros_like(Q_Values)
    FrontUU_Unc = np.zeros_like(Q_Values)
    FrontMeanQ = np.zeros_like(Q_Values)
    FrontMeanQUnc = np.zeros_like(Q_Values)
    FrontPixels = np.zeros_like(Q_Values)
    
    MiddleUU = np.zeros_like(Q_Values)
    MiddleUU_Unc = np.zeros_like(Q_Values)
    MiddleMeanQ = np.zeros_like(Q_Values)
    MiddleMeanQUnc = np.zeros_like(Q_Values)
    MiddlePixels = np.zeros_like(Q_Values)
    
    for dshort in short_detectors:
        Q_tot = QGridPerDetector['Q_total'][dshort][:][:]
        Q_unc = np.sqrt(np.power(QGridPerDetector['Q_perp_unc'][dshort][:][:],2) + np.power(QGridPerDetector['Q_parl_unc'][dshort][:][:],2))
        UU = PolCorr_AllDetectors[dshort][:][:]
        UU_Unc = Unc_PolCorr_AllDetectors[dshort][:][:]

        Exp_bins = np.linspace(Q_min, Q_max + Q_step, Q_bins + 1, endpoint=True)
        countsUU, _ = np.histogram(Q_tot[masks[dshort] > 0], bins=Exp_bins, weights=UU[masks[dshort] > 0])
        
        UncUU, _ = np.histogram(Q_tot[masks[dshort] > 0], bins=Exp_bins, weights=np.power(UU_Unc[masks[dshort] > 0],2))
        
        MeanQSum, _ = np.histogram(Q_tot[masks[dshort] > 0], bins=Exp_bins, weights=Q_tot[masks[dshort] > 0])
        MeanQUnc, _ = np.histogram(Q_tot[masks[dshort] > 0], bins=Exp_bins, weights=np.power(Q_unc[masks[dshort] > 0],2)) 
        pixels, _ = np.histogram(Q_tot[masks[dshort] > 0], bins=Exp_bins, weights=np.ones_like(UU)[masks[dshort] > 0])
        
        carriage_key = dshort[0]
        if carriage_key == 'F':
            FrontUU += countsUU
            FrontUU_Unc += UncUU
            FrontMeanQ += MeanQSum
            FrontMeanQUnc += MeanQUnc
            FrontPixels += pixels
        elif carriage_key == 'M':
            MiddleUU += countsUU
            MiddleUU_Unc += UncUU
            MiddleMeanQ += MeanQSum
            MiddleMeanQUnc += MeanQUnc
            MiddlePixels += pixels

    CombinedPixels = FrontPixels + MiddlePixels
    nonzero_front_mask = (FrontPixels > 0) #True False map
    nonzero_middle_mask = (MiddlePixels > 0) #True False map
    nonzero_combined_mask = (CombinedPixels > 0) #True False map
    
    Q_Front = Q_Values[nonzero_front_mask]
    MeanQ_Front = FrontMeanQ[nonzero_front_mask] / FrontPixels[nonzero_front_mask]
    MeanQUnc_Front = np.sqrt(FrontMeanQUnc[nonzero_front_mask]) / FrontPixels[nonzero_front_mask]
    UUF = FrontUU[nonzero_front_mask] / FrontPixels[nonzero_front_mask]
    
    Q_Middle = Q_Values[nonzero_middle_mask]
    MeanQ_Middle = MiddleMeanQ[nonzero_middle_mask] / MiddlePixels[nonzero_middle_mask]
    MeanQUnc_Middle = np.sqrt(MiddleMeanQUnc[nonzero_middle_mask]) / MiddlePixels[nonzero_middle_mask]
    UUM = MiddleUU[nonzero_middle_mask] / MiddlePixels[nonzero_middle_mask]

    Sigma_UUF = np.sqrt(FrontUU_Unc[nonzero_front_mask]) / FrontPixels[nonzero_front_mask]
    Sigma_UUM = np.sqrt(MiddleUU_Unc[nonzero_middle_mask]) / MiddlePixels[nonzero_middle_mask]

    ErrorBarsYesNo = 0
    if PlotYesNo == 1:
        fig = plt.figure()
        if ErrorBarsYesNo == 1:
            ax = plt.axes()
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.errorbar(Q_Front, UUF, yerr=Sigma_UUF, fmt = 'b*', label='Front, UU')
            ax.errorbar(Q_Middle, UUM, yerr=Sigma_UUM, fmt = 'g*', label='Middle, UU')
        else:
            plt.loglog(Q_Front, UUF, 'b*', label='Front, UU')
            plt.loglog(Q_Middle, UUM, 'g*', label='Middle, UU')
        plt.xlabel('Q')
        plt.ylabel('Intensity')
        plt.title('FullPol_{keyword}Cuts for ID = {idnum} and Config = {cf}'.format(keyword=Key, idnum=ID, cf = Config))
        plt.legend()
        fig.savefig('{keyword}FullPol_Cuts_ID{idnum}_CF{cf}.png'.format(keyword=Key, idnum=ID, cf = Config))
        plt.show()

    Q_Common = Q_Values[nonzero_combined_mask]
    CombinedMeanQ = MiddleMeanQ + FrontMeanQ
    CombinedMeanQUnc = MiddleMeanQUnc + FrontMeanQUnc  
    Q_Mean = CombinedMeanQ[nonzero_combined_mask] / CombinedPixels[nonzero_combined_mask]
    Q_Uncertainty = np.sqrt(CombinedMeanQUnc[nonzero_combined_mask]) / CombinedPixels[nonzero_combined_mask]
    CombinedUU = MiddleUU + FrontUU
    UU = CombinedUU[nonzero_combined_mask] / CombinedPixels[nonzero_combined_mask]
    UU_UncC = FrontUU_Unc + MiddleUU_Unc
    SigmaUU = np.sqrt(UU_UncC[nonzero_combined_mask]) / CombinedPixels[nonzero_combined_mask]
    Shadow = np.ones_like(Q_Common)

    Output = {}
    Output['Q'] = Q_Common
    Output['Q_Mean'] = Q_Mean
    Output['I'] = UU
    Output['I_Unc'] = SigmaUU
    Output['Q_Uncertainty'] = Q_Uncertainty
    Output['Shadow'] = Shadow
     
    return Output

def Raw_Data(filenumber):

    RawData_AllDetectors = {}
    Unc_RawData_AllDetectors = {}

    filename = path + "sans" + str(filenumber) + ".nxs.ngv"
    config = Path(filename)
    if config.is_file():
        f = h5py.File(filename)

        for dshort in short_detectors:
            data = np.array(f['entry/instrument/detector_{ds}/data'.format(ds=dshort)])
            RawData_AllDetectors[dshort] = data
            Unc_RawData_AllDetectors[dshort] = np.sqrt(data)
                    
    return RawData_AllDetectors, Unc_RawData_AllDetectors

def ASCIIlike_Output(Type, ID, Config, Data_AllDetectors, Unc_Data_AllDetectors, QGridPerDetector, GeneralMask):

    if 'NA' not in Data_AllDetectors and 'NA' not in Unc_Data_AllDetectors:
        for dshort in short_detectors:

            Mask = np.array(GeneralMask[dshort])
            mini_mask = Mask > 0

            Q_tot = QGridPerDetector['Q_total'][dshort][:][:]
            Q_unc = np.sqrt(np.power(QGridPerDetector['Q_perp_unc'][dshort][:][:],2) + np.power(QGridPerDetector['Q_parl_unc'][dshort][:][:],2))

            QQX = QGridPerDetector['QX'][dshort][:][:]
            QQX = QQX[mini_mask,...]
            QQX = QQX.T
            QXData = QQX.flatten()
            QQY = QGridPerDetector['QY'][dshort][:][:]
            QQY = QQY[mini_mask,...]
            QQY = QQY.T
            QYData = QQY.flatten()
            QQZ = QGridPerDetector['QZ'][dshort][:][:]
            QQZ = QQZ[mini_mask,...]
            QQZ = QQZ.T
            QZData = QQZ.flatten()
            QPP = QGridPerDetector['Q_perp_unc'][dshort][:][:]
            QPP = QPP[mini_mask,...]
            QPP = QPP.T
            QPerpUnc = QPP.flatten()
            QPR = QGridPerDetector['Q_parl_unc'][dshort][:][:]
            QPR = QPR[mini_mask,...]
            QPR = QPR.T
            QParlUnc = QPR.flatten()
            Shadow = np.ones_like(Q_tot)
            Shadow = Shadow[mini_mask,...]
            Shadow = Shadow.T
            ShadowHolder = Shadow.flatten()

            Intensity = Data_AllDetectors[dshort]
            Intensity = Intensity[mini_mask,...]
            Intensity = Intensity.T
            Int = Intensity.flatten()
            Intensity = Intensity.flatten()
            IntensityUnc = Unc_Data_AllDetectors[dshort]
            IntensityUnc = IntensityUnc[mini_mask,...]
            IntensityUnc = IntensityUnc.T
            DeltaInt = IntensityUnc.flatten()
            IntensityUnc = IntensityUnc.flatten()
            if YesNo_2DFilesPerDetector > 0:
                print('Outputting Unpol data into ASCII-like format for {det}, GroupID = {idnum} '.format(det=dshort, idnum=ID))
                ASCII_like = np.array([QXData, QYData, Int, DeltaInt, QZData, QParlUnc, QPerpUnc, ShadowHolder])
                ASCII_like = ASCII_like.T
                np.savetxt('{TP}Scatt_{Samp}_{CF}_{det}.DAT'.format(TP=Type, Samp=ID, CF=Config, det=dshort), ASCII_like, delimiter = ' ', comments = ' ', header = 'ASCII data created Mon, Jan 13, 2020 2:39:54 PM')
           

            if dshort == short_detectors[0]:
                Int_Combined = Intensity
                DeltaInt_Combined = IntensityUnc
                QXData_Combined = QXData
                QYData_Combined = QYData
                QZData_Combined = QZData
                QPP_Combined = QPP
                QPerpUnc_Combined = QPerpUnc
                QPR_Combined = QPR
                QParlUnc_Combined = QParlUnc
                Shadow_Combined = ShadowHolder
            else:
                Int_Combined = np.concatenate((Int_Combined, Intensity), axis=0)
                DeltaInt_Combined = np.concatenate((DeltaInt_Combined, IntensityUnc), axis=0)
                QXData_Combined = np.concatenate((QXData_Combined, QXData), axis=0)
                QYData_Combined = np.concatenate((QYData_Combined, QYData), axis=0)
                QZData_Combined = np.concatenate((QZData_Combined, QZData), axis=0)
                QPP_Combined = np.concatenate((QPP_Combined, QPP), axis=0)
                QPerpUnc_Combined = np.concatenate((QPerpUnc_Combined, QPerpUnc), axis=0)
                QPR_Combined = np.concatenate((QPR_Combined, QPR), axis=0)
                QParlUnc_Combined = np.concatenate((QParlUnc_Combined, QParlUnc), axis=0)
                Shadow_Combined = np.concatenate((Shadow_Combined, ShadowHolder), axis=0)

        print('Outputting {TP} 2D data, {idnum}, {CF} '.format(TP=Type, idnum=ID, CF=Config))        
        ASCII_Combined = np.array([QXData_Combined, QYData_Combined, Int_Combined, DeltaInt_Combined, QZData_Combined, QParlUnc_Combined, QPerpUnc_Combined, Shadow_Combined])
        ASCII_Combined = ASCII_Combined.T
        np.savetxt('{TP}Scatt_{Samp}_{CF}.DAT'.format(TP=Type, Samp=ID, CF=Config), ASCII_Combined, delimiter = ' ', comments = ' ', header = 'ASCII data created Mon, Jan 13, 2020 2:39:54 PM')

    return

def PlotAndSaveFullPolSlices(Sample, Config, InPlaneAngleMap, Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, PolCorrUU, PolCorrUU_Unc, PolCorrDU, PolCorrDU_Unc, PolCorrDD, PolCorrDD_Unc, PolCorrUD, PolCorrUD_Unc):

    BothSides = 1
    HorzMask = SectorMask_AllDetectors(InPlaneAngleMap, 0, SectorCutAngles, BothSides)
    VertMask = SectorMask_AllDetectors(InPlaneAngleMap, 180, SectorCutAngles, BothSides)

    PlotYesNo = 0
    UUHorz = TwoDimToOneDim('Horz', Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, HorzMask, PolCorrUU, PolCorrUU_Unc, Sample, Config, PlotYesNo)
    DUHorz = TwoDimToOneDim('Horz', Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, HorzMask, PolCorrDU, PolCorrDU_Unc, Sample, Config, PlotYesNo)
    DDHorz = TwoDimToOneDim('Horz', Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, HorzMask, PolCorrDD, PolCorrDD_Unc, Sample, Config, PlotYesNo)
    UDHorz = TwoDimToOneDim('Horz', Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, HorzMask, PolCorrUD, PolCorrUD_Unc, Sample, Config, PlotYesNo)

    Q = UUHorz['Q']
    UUCut = UUHorz['I']
    UUCutUnc = UUHorz['I_Unc']
    DUCut = DUHorz['I']
    DUCutUnc = DUHorz['I_Unc']
    DDCut = DDHorz['I']
    DDCutUnc = DDHorz['I_Unc']
    UDCut = UDHorz['I']
    UDCutUnc = UDHorz['I_Unc']
    Q_mean = UUHorz['Q_Mean']
    Q_Unc = UUHorz['Q_Uncertainty']
    Shadow = np.ones_like(Q)
    text_output = np.array([Q, UUCut, UUCutUnc, DUCut, DUCutUnc, DDCut, DDCutUnc, UDCut, UDCutUnc, Q_mean, Q_Unc, Shadow])
    text_output = text_output.T
    np.savetxt('FullPolHorz{deg}_{idnum}_{cf}.txt'.format(deg=SectorCutAngles, idnum=Sample, cf = Config), text_output, header= 'Q, UU, DelUU, DU, DelDU, DD, DelDD, UD, DelUD, Q_mean, Q_Unc, Shadow', fmt='%1.4e')
    
    fig = plt.figure()
    ax = plt.axes()
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.errorbar(UUHorz['Q'], UUHorz['I'], yerr=UUHorz['I_Unc'], fmt = 'b*', label='UU Horz')
    ax.errorbar(DUHorz['Q'], DUHorz['I'], yerr=DUHorz['I_Unc'], fmt = 'g*', label='DU Horz')
    ax.errorbar(DDHorz['Q'], DDHorz['I'], yerr=DDHorz['I_Unc'], fmt = 'r*', label='DD Vert')
    ax.errorbar(UDHorz['Q'], UDHorz['I'], yerr=UDHorz['I_Unc'], fmt = 'm*', label='UD Horz')
    plt.xlabel('Q')
    plt.ylabel('Intensity')
    plt.title('Horz Slices')
    plt.legend()
    fig.savefig('FullPol_Horz{Deg}_{idnum}_{cf}.png'.format(Deg=SectorCutAngles, idnum=Sample, cf = Config))
    plt.show()

    return

#*************************************************
#***        Start of 'The Program'             ***
#*************************************************

FILE_STORE.update(LoadVSANSFiles())
Sample_Names, Configs, BlockBeam, Scatt, Trans, Pol_Trans, HE3_Trans, start_number, filenumberlisting = SortDataAutomatic(YesNoManualHe3Entry, New_HE3_Files, MuValues, TeValues)

Process_ScattFiles()

Masks = ReadIn_Masks(filenumberlisting)

Process_Transmissions(BlockBeam, Masks, HE3_Trans, Pol_Trans, Trans)

HE3_Cell_Summary = HE3_DecayCurves(HE3_Trans)

Pol_SuppermirrorAndFlipper(Pol_Trans, HE3_Cell_Summary)

Plex = Plex_File(start_number)

GeneralMaskWOSolenoid = {}
GeneralMaskWSolenoid = {}
QValues_All = {}
for Config in Configs:
    representative_filenumber = Configs[Config]
    if representative_filenumber != 0:
        Solid_Angle = SolidAngle_AllDetectors(representative_filenumber)
        BB_per_second = BlockedBeamScattCountsPerSecond(Config, representative_filenumber)
        Qx, Qy, Qz, Q_total, Q_perp_unc, Q_parl_unc, InPlaneAngleMap, dimXX, dimYY = QCalculation_AllDetectors(representative_filenumber)
        QValues_All = {'QX':Qx,'QY':Qy,'QZ':Qz,'Q_total':Q_total,'Q_perp_unc':Q_perp_unc,'Q_parl_unc':Q_parl_unc}
        Q_minCalc, Q_maxCalc = MinMaxQ(Q_total)
        Q_min = np.maximum(Absolute_Q_min, Q_minCalc)
        Q_max = np.minimum(Absolute_Q_max, Q_maxCalc)
        Q_bins = int(150*(Q_max - Q_min)/(Q_maxCalc - Q_minCalc))
        for dshort in short_detectors:
            GeneralMaskWOSolenoid[dshort] = np.ones_like(Qx[dshort])
            GeneralMaskWSolenoid[dshort] = np.ones_like(Qx[dshort])
        if Config in Masks:
            if 'NA' not in Masks[Config]['Scatt_WithSolenoid']:
                for dshort in short_detectors:
                    GeneralMaskWSolenoid[dshort] = Masks[Config]['Scatt_WithSolenoid'][dshort]
            if 'NA' not in Masks[Config]['Scatt_Standard']:
                for dshort in short_detectors:
                    GeneralMaskWOSolenoid[dshort] = Masks[Config]['Scatt_Standard'][dshort]
            if 'NA' in Masks[Config]['Scatt_WithSolenoid'] and 'NA' not in Masks[Config]['Scatt_Standard']:
                for dshort in short_detectors:
                    GeneralMaskWSolenoid[dshort] = Masks[Config]['Scatt_Standard'][dshort]
  
        for Sample in Sample_Names:
            if Sample in Scatt:                
                if str(Scatt[Sample]['Intent']).find('Sample') != -1:
                    
                    UUScaledData, UUScaledData_Unc = AbsScale('UU', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    DUScaledData, DUScaledData_Unc = AbsScale('DU', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    DDScaledData, DDScaledData_Unc = AbsScale('DD', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    UDScaledData, UDScaledData_Unc = AbsScale('UD', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    FullPolGo = 0
                    if 'NA' not in UUScaledData and 'NA' not in DUScaledData and 'NA' not in DDScaledData and 'NA' not in UDScaledData:
                        FullPolGo, PolCorrUU, PolCorrDU, PolCorrDD, PolCorrUD, PolCorrUU_Unc, PolCorrDU_Unc, PolCorrDD_Unc, PolCorrUD_Unc = PolCorrScattFiles(dimXX, dimYY, Sample, Config, UUScaledData, DUScaledData, DDScaledData, UDScaledData, UUScaledData_Unc, DUScaledData_Unc, DDScaledData_Unc, UDScaledData_Unc)
                        if FullPolGo > 0:
                            ASCIIlike_Output('PolCorrUU', Sample, Config, PolCorrUU, PolCorrUU_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('PolCorrDU', Sample, Config, PolCorrDU, PolCorrDU_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('PolCorrDD', Sample, Config, PolCorrDD, PolCorrDD_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('PolCorrUD', Sample, Config, PolCorrUD, PolCorrUD_Unc, QValues_All, GeneralMaskWSolenoid)
                            PlotAndSaveFullPolSlices(Sample, Config, InPlaneAngleMap, Q_min, Q_max, Q_bins, QValues_All, GeneralMaskWSolenoid, PolCorrUU, PolCorrUU_Unc, PolCorrDU, PolCorrDU_Unc, PolCorrDD, PolCorrDD_Unc, PolCorrUD, PolCorrUD_Unc)
                        else:
                            ASCIIlike_Output('NotPolCorrUU', Sample, Config, UUScaledData, UUScaledData_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('NotPolCorrDU', Sample, Config, DUScaledData, DUScaledData_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('NotPolCorrDD', Sample, Config, DDScaledData, DDScaledData_Unc, QValues_All, GeneralMaskWSolenoid)
                            ASCIIlike_Output('NotPolCorrUD', Sample, Config, UDScaledData, UDScaledData_Unc, QValues_All, GeneralMaskWSolenoid)

                    
                    UScaledData, UScaledData_Unc = AbsScale('U', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    DScaledData, DScaledData_Unc = AbsScale('D', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    if 'NA' not in UScaledData and 'NA' not in DScaledData: #Scatt[Sample]['Config(s)'][Config]['UU']:
                        representative_filenumber = Scatt[Sample]['Config(s)'][Config]['U'][0]
                        Qx, Qy, Qz, Q_total, Q_perp_unc, Q_parl_unc, InPlaneAngleMap, dimXX, dimYY = QCalculation_AllDetectors(representative_filenumber)
                        QValues_All = {'QX':Qx,'QY':Qy,'QZ':Qz,'Q_total':Q_total,'Q_perp_unc':Q_perp_unc,'Q_parl_unc':Q_parl_unc}
                        ASCIIlike_Output('U', Sample, Config, UScaledData, UScaledData_Unc, QValues_All, GeneralMaskWOSolenoid)
                        ASCIIlike_Output('D', Sample, Config, DScaledData, DScaledData_Unc, QValues_All, GeneralMaskWOSolenoid)

                    UnpolScaledData, UnpolScaledData_Unc = AbsScale('Unpol', Sample, Config, BB_per_second, Solid_Angle, Plex)
                    if 'NA' not in UnpolScaledData: #Scatt[Sample]['Config(s)'][Config]['Unpol']:
                        representative_filenumber = Scatt[Sample]['Config(s)'][Config]['Unpol'][0]
                        Qx, Qy, Qz, Q_total, Q_perp_unc, Q_parl_unc, InPlaneAngleMap, dimXX, dimYY = QCalculation_AllDetectors(representative_filenumber)
                        QValues_All = {'QX':Qx,'QY':Qy,'QZ':Qz,'Q_total':Q_total,'Q_perp_unc':Q_perp_unc,'Q_parl_unc':Q_parl_unc}
                        ASCIIlike_Output('Unpol', Sample, Config, UnpolScaledData, UnpolScaledData_Unc, QValues_All, GeneralMaskWOSolenoid)
                        
                    


                                 
#*************************************************
#***           End of 'The Program'            ***
#*************************************************



