import psy_tools as ps
import matplotlib.pyplot as plt
from alex_utils import *
plt.ion()

ps.plot_session_summary(IDS)
ps.plot_session_summary(IDS,savefig=True)

# Start of filtering my session type
stages = ps.get_stage_names(IDS) # Takes Forever
ps.plot_session_summary(stages[1]+stages[3],savefig=True,group_label="A_")
ps.plot_session_summary(stages[4],savefig=True,group_label="B1_")
ps.plot_session_summary(stages[6],savefig=True,group_label="B2_")
ps.plot_session_summary(stages[4]+stages[6],savefig=True,group_label="B_")
good_IDS = ps.get_good_behavior_IDS(IDS) 
ps.plot_session_summary(good_IDS,savefig=True,group_label="hits_100_")
# TODO
# 1. Make a function that formats just one session (for behavior sessions)
# 2. Summarize fits over time
# 3. Update Overleaf

r = np.zeros((25,25))
for i in np.arange(1,25,1):
    for j in np.arange(1,25,1):
        r[i,j] = ps.compute_model_prediction_correlation(fit, fit_mov=i,data_mov=j)



import pandas as pd
behavior_sessions = pd.read_hdf('/home/nick.ponvert/nco_home/data/20190626_sessions_to_load.h5', key='df')
all_flash_df = pd.read_hdf('/home/nick.ponvert/nco_home/data/20190626_all_flash_df.h5', key='df')
behavior_psydata = ps.format_all_sessions(all_flash_df)
hyp2, evd2, wMode2, hess2, credibleInt2,weights2 = ps.fit_weights(behavior_psydata,TIMING4=True)
ypred2 = ps.compute_ypred(behavior_psydata, wMode2,weights2)
ps.plot_weights(wMode2, weights2,behavior_psydata,errorbar=credibleInt2, ypred = ypred2,validation=False,session_labels = behavior_sessions.stage_name.values)





for mouse in mice:
    print(mouse)
    try:
        ps.process_mouse(mouse)
    except Exception as e:
        print(str(mouse) + " " + str(e))
    plt.close('all')




mice = [722884873, 744911447, 756577240, 756674776, 760949537, 766015379,
       772622642, 772629800, 784057617, 789992895, 791756316, 795512663,
       795522266, 803258370, 813703535, 814111925, 820871399, 820878203,
       823826963, 830896318, 830901414, 830940312, 831004160, 834823464,
       842724844, 843387577, 847074139, 847076515]




IDS = [842513687, 841951447, 841601446, 840705705, 840157581, 839653299, 839387525, 837730373, 837298553, 836909490, 836260129, 835008543, 834275038, 833631932, 898747791, 894727297, 893825996, 892799212, 891994418, 891052180, 889771676, 894726001, 893831526, 892805315, 891996193, 891054695, 889772922, 864370674, 862848066, 859148154, 858421897, 857660011, 856938751, 856096766, 855582981, 853325726, 852691507, 851933686, 851060485, 850462509, 849203586, 848697604, 869969410, 863735602, 860030092, 859147033, 858420173, 857658471, 855577488, 854703305, 853962951, 853328115, 852689561, 851932072, 895422170, 894724572, 893830418, 892793301, 891065492, 889775726, 888682725, 887362436, 886544609, 885933209, 885067016, 884221487, 882519987, 880961028, 880374622, 879331157, 878363088, 877697554, 877022592, 875732176, 875045646, 873972085, 873156540, 872433717, 871155338, 869964292, 880961028, 880374622, 879331157, 878363088, 877697554, 877022592, 875732176, 875045646, 873972085, 873156540, 872433717, 871155338, 869964292, 826585773, 825623170, 825120601, 823392290, 822641265, 822024770, 815096009, 813083494, 810119703, 809500564, 809196647, 808619526, 807752701, 806990245, 806456625, 884218326, 882935355, 881881171, 880375092, 879332693, 878363070, 877696762, 877022583, 875732195, 819432482, 818904275, 817263470, 816847460, 813083478, 811988977, 811456530, 810738327, 809500093, 809195587, 808619543, 807752719, 806989088, 806456687, 837729902, 837296345, 836258936, 835740405, 835006071, 834279496, 833629926, 831324619, 830700781, 830093355, 875729873, 873968801, 868899428, 868231423, 878358326, 877691075, 877018118, 875729856, 875045489, 873968820, 873154932, 871154421, 869969393, 868905381, 868231440, 867343333, 866929742, 791974731, 792813858, 793738343, 794381992, 795076128, 795952471, 796105304, 797255551, 811458048, 809501118, 809195570, 808621034, 807753334, 806989729, 806455766, 783927872, 784482326, 787501804, 787498290, 789359614, 791983479, 794389520, 795075053, 795948257, 798398183, 799368904, 788489531, 782675457, 782675436, 809497730, 808621958, 807753318, 805784331, 805100414, 796107403, 796307952, 797254124, 798392580, 799366535, 800035827, 800398152, 803736273, 838849930, 836910438, 836260147, 835738362, 835008526, 834275020, 833629942, 832802571, 832117336, 831330384, 791980891, 792815735, 793738380, 795073741, 795953296, 796108483, 796308505, 798404219, 776413956, 778064481, 791450265, 793733895, 796106850, 788490510, 775614751, 778644591, 779327555, 780955942, 787461073, 784476477, 787521328, 786488799, 792812544, 836911939, 836258957, 835740423, 833631914, 832115263, 830702018, 830700800, 830093338, 828965691, 827238524, 826587940, 825130141, 823396880, 822647135, 822032328, 820307518, 819434449, 819434439, 818908650, 817267785, 816846652, 815652334, 815097967, 893831541, 892793263, 892001352, 891067673, 889775742, 888666698, 887357514, 885067844, 882932458, 882520593, 880960336, 853962969, 852689577, 851932055, 851056106, 850489605, 849199228, 848692970, 896160394, 895422187, 893830436, 892801488, 892001369, 891067646, 889777243, 888666715, 887362952, 885933191, 885067826, 884221469, 853965818, 848698709, 848694639, 847125577, 846484396, 845020615, 843519218, 842973730, 848694045, 847241639, 846487947, 845037476, 844395446, 843520488, 842975542, 842510806, 855582961, 852691524, 851933703, 851060467, 850479305, 849203565, 848697625]

