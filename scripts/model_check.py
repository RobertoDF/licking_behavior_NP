##########################
# Check if all fits have completed:

experiment_ids = [792813858, 792815735, 794381992, 795073741, 795076128, 795952471,
       795953296, 796105304, 796108483, 796308505, 797255551, 798404219,
       803736273, 805784331, 806455766, 806456687, 806989729, 807752719,
       807753318, 807753334, 808619543, 808621034, 808621958, 809497730,
       809501118, 811456530, 811458048, 813083478, 815652334, 817267785,
       820307518, 822647135, 825130141, 826587940, 830093338, 830700781,
       830700800, 833629926, 833631914, 834279496, 836258936, 836258957,
       836911939, 837296345, 837729902, 842973730, 843519218, 847125577,
       848692970, 848694639, 848697604, 848697625, 848698709, 849199228,
       849203565, 849203586, 850479305, 850489605, 851056106, 851060467,
       851932055, 852691524, 853328115, 853962951, 853962969, 854703305,
       855577488, 855582961, 855582981, 856096766, 859147033, 862848066,
       863735602, 864370674, 873972085, 877022592, 878363088, 879331157,
       880374622, 880961028]
mouse_ids= [744911447,756674776,760949537,772622642,772629800,784057617,789992895,791756316,803258370,813703535,820871399,820878203,823826963,834823464]

import psy_tools as ps
import os
dir7="/home/alex.piet/codebase/behavior/psy_fits_v7/"
dir8="/home/alex.piet/codebase/behavior/psy_fits_v8/"


def check_sessions(experiment_ids, mouse_ids,dir):
    print('The following sessions need to be fit')
    passive = ps.get_passive_ids()
    not_complete = []
    for id in experiment_ids:
        if not os.path.isfile(dir+str(id)+".pkl"):
            if id not in passive:
                print(id)
                not_complete.append(id)
    
    print('The following mice need to be fit')
    not_complete_mice = []
    for id in mouse_ids:
        if not os.path.isfile(dir+"mouse_"+str(id)+".pkl"):
            print(id)
            not_complete_mice.append(id)

check_sessions(experiment_ids, mouse_ids, dir7)
check_sessions(experiment_ids, mouse_ids, dir8)


