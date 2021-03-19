import numpy as np
import matplotlib.pyplot as plt
import psy_general_tools as pgt
import seaborn as sns
import pandas as pd
import matplotlib.patches as patches
import scipy.stats as ss
from scipy.stats import norm
from scipy import stats
from tqdm import tqdm

'''
This is a set of functions for calculating and analyzing model free behavioral metrics on a flash by flash basis
Alex Piet, alexpiet@gmail.com
11/5/2019

'''
MODEL_FREE_DIR = '/home/alex.piet/codebase/behavior/model_free/'

def get_metrics(session,add_running=False):
    '''
        Top level function that appends a few columns to session.stimulus_presentations,
            and a few columns to session.licks 

        ARGUMENTS: session, SDK session object
        
        Adds to session.licks
            pre_ili,        (seconds)
            post_ili,       (seconds)
            rewarded,       (boolean)
            bout_start,     (boolean)
            bout_end,       (boolean)
            bout_number,    (int)
            bout_rewarded,  (boolean)

        Adds to session.stimulus_presentations
            bout_start,     (boolean)
            bout_end,       (boolean)
            licked,         (boolean)
            lick_rate,      (licks/flash)
            rewarded,       (boolean)
            reward_rate,    (rewards/flash)
            running_rate,
            bout_rate,      (bouts/flash)
            high_lick,      (boolean)
            high_reward,    (boolean)
            flash_metrics_epochs, (int)
            flash_metrics_labels, (string)
    '''
    annotate_licks(session)
    annotate_bouts(session)
    annotate_flash_rolling_metrics(session,add_running=add_running)
    classify_by_flash_metrics(session)

def annotate_licks(session,bout_threshold=0.7):
    '''
        Appends several columns to session.licks. Calculates licking bouts based on a
        interlick interval (ILI) of bout_threshold. Default of 700ms based on examining 
        histograms of ILI distributions

        Adds to session.licks
            pre_ili,        (seconds)
            post_ili,       (seconds)
            rewarded,       (boolean)
            bout_start,     (boolean)
            bout_end,       (boolean)
            bout_number,    (int)
            bout_rewarded,  (boolean)
    '''

    # Something was buggy upon repeated re-annotations, so I throw an error
    if 'bout_number' in session.licks:
        raise Exception('You already annotated this session, reload session first')

    # Computing ILI for each lick 
    licks = session.licks
    licks['pre_ili'] = np.concatenate([[np.nan],np.diff(licks.timestamps.values)])
    licks['post_ili'] = np.concatenate([np.diff(licks.timestamps.values),[np.nan]])
    licks['rewarded'] = False
    for index, row in session.rewards.iterrows():
        if len(np.where(licks.timestamps<=row.timestamps)[0]) == 0:
            if (row.autorewarded) & (row.timestamps <= licks.timestamps.values[0]):
                # mouse hadn't licked before first auto-reward
                mylick = 0
            else:
                print('First lick was after first reward, but it wasnt an auto-reward. This is very strange, but Im annotating the first lick as rewarded.')
                mylick = 0
        else:
            mylick = np.where(licks.timestamps <= row.timestamps)[0][-1]
        licks.at[mylick,'rewarded'] = True
    
    # Segment licking bouts
    licks['bout_start'] = licks['pre_ili'] > bout_threshold
    licks['bout_end'] = licks['post_ili'] > bout_threshold
    licks.at[licks['pre_ili'].apply(np.isnan),'bout_start']=True
    licks.at[licks['post_ili'].apply(np.isnan),'bout_end']=True

    # Annotate bouts by number, and reward
    licks['bout_number'] = np.cumsum(licks['bout_start'])
    x = session.licks.groupby('bout_number').any('rewarded').rename(columns={'rewarded':'bout_rewarded'})
    session.licks['bout_rewarded'] = False
    temp = session.licks.reset_index().set_index('bout_number')
    temp.update(x)
    temp = temp.reset_index().set_index('index')
    session.licks['bout_rewarded'] = temp['bout_rewarded']

def annotate_bouts(session):
    '''
        Uses the bout annotations in session.licks to annotate session.stimulus_presentations

        Adds to session.stimulus_presentations
            bout_start,     (boolean)
            bout_end,       (boolean)

    '''
    # Annotate Bout Starts
    bout_starts = session.licks[session.licks['bout_start']]
    session.stimulus_presentations['bout_start'] = False
    session.stimulus_presentations['num_bout_start'] = 0
    for index,x in bout_starts.iterrows():
        filter_start = session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)]
        if (x.timestamps > session.stimulus_presentations.iloc[0].start_time ) & (len(filter_start) > 0):
            session.stimulus_presentations.at[session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)].index[0]-1,'bout_start'] = True
            session.stimulus_presentations.at[session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)].index[0]-1,'num_bout_start'] += 1
    # Annotate Bout Ends
    bout_ends = session.licks[session.licks['bout_end']]
    session.stimulus_presentations['bout_end'] = False
    session.stimulus_presentations['num_bout_end'] = 0
    for index,x in bout_ends.iterrows():
        filter_start = session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)]
        if (x.timestamps > session.stimulus_presentations.iloc[0].start_time) & (len(filter_start) > 0):
            session.stimulus_presentations.at[session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)].index[0]-1,'bout_end'] = True
            session.stimulus_presentations.at[session.stimulus_presentations[session.stimulus_presentations['start_time'].gt(x.timestamps)].index[0]-1,'num_bout_end'] += 1
            # Check to see if bout started before stimulus, if so, make first flash as bout_starts
            bout_start_time = session.licks.query('bout_number == @x.bout_number').query('bout_start').timestamps.values[0]
            bout_end_time = x.timestamps
            if (bout_start_time < session.stimulus_presentations.iloc[0].start_time) & (bout_end_time > session.stimulus_presentations.iloc[0].start_time):
                session.stimulus_presentations.at[0,'bout_start'] = True
                session.stimulus_presentations.at[0,'num_bout_start'] += 1
    # Clean Up
    session.stimulus_presentations.drop(-1,inplace=True,errors='ignore')

def annotate_bout_start_time(session):
    session.stimulus_presentations['bout_start_time'] = np.nan
    session.stimulus_presentations.at[session.stimulus_presentations['bout_start'] == True,'bout_start_time'] = session.stimulus_presentations[session.stimulus_presentations['bout_start']==True].licks.str[0]
    

def annotate_flash_rolling_metrics(session,win_dur=320, win_type='triang', add_running=False):
    '''
        Get rolling flash level metrics for lick rate, reward rate, and bout_rate
        Computes over a rolling window of win_dur (s) duration, with a window type given by win_type

        Adds to session.stimulus_presentations
            licked,         (boolean)
            lick_rate,      (licks/flash)
            rewarded,       (boolean)
            reward_rate,    (rewards/flash)
            running_rate,   (cm/s)
            bout_rate,      (bouts/flash)
    '''
    # Get Lick Rate / second
    session.stimulus_presentations['licked'] = [1 if len(this_lick) > 0 else 0 for this_lick in session.stimulus_presentations['licks']]
    session.stimulus_presentations['lick_rate'] = session.stimulus_presentations['licked'].rolling(win_dur, min_periods=1,win_type=win_type).mean()/.75

    # Get Reward Rate / second
    session.stimulus_presentations['rewarded'] = [1 if len(this_reward) > 0 else 0 for this_reward in session.stimulus_presentations['rewards']]
    session.stimulus_presentations['reward_rate'] = session.stimulus_presentations['rewarded'].rolling(win_dur,min_periods=1,win_type=win_type).mean()/.75

    # Get Running / Second
    if add_running:
        session.stimulus_presentations['running_rate'] = session.stimulus_presentations['mean_running_speed'].rolling(win_dur,min_periods=1,win_type=win_type).mean()/.75

    # Get Bout Rate / second
    session.stimulus_presentations['bout_rate'] = session.stimulus_presentations['bout_start'].rolling(win_dur,min_periods=1, win_type=win_type).mean()/.75

    # Get Hit Fraction. % of licks that are rewarded
    session.stimulus_presentations['hit_bout'] = [np.nan if (not x[0]) else 1 if (x[1]==1) else 0 for x in zip(session.stimulus_presentations['bout_start'], session.stimulus_presentations['rewarded'])]
    session.stimulus_presentations['hit_fraction'] = session.stimulus_presentations['hit_bout'].rolling(win_dur,min_periods=1,win_type=win_type).mean().fillna(0)
    
    # Get Hit Rate, % of change flashes with licks
    session.stimulus_presentations['change_with_lick'] = [np.nan if (not x[0]) else 1 if (x[1]) else 0 for x in zip(session.stimulus_presentations['change'],session.stimulus_presentations['bout_start'])]
    session.stimulus_presentations['hit_rate'] = session.stimulus_presentations['change_with_lick'].rolling(win_dur,min_periods=1,win_type=win_type).mean().fillna(0)
  
    # Get Miss Rate, % of change flashes without licks
    session.stimulus_presentations['change_without_lick'] = [np.nan if (not x[0]) else 0 if (x[1]) else 1 for x in zip(session.stimulus_presentations['change'],session.stimulus_presentations['bout_start'])]
    session.stimulus_presentations['miss_rate'] = session.stimulus_presentations['change_without_lick'].rolling(win_dur,min_periods=1,win_type=win_type).mean().fillna(0)

    # Get False Alarm Rate, % of non-change flashes with licks
    session.stimulus_presentations['non_change_with_lick'] = [np.nan if (x[0]) else 1 if (x[1]) else 0 for x in zip(session.stimulus_presentations['change'],session.stimulus_presentations['bout_start'])]
    session.stimulus_presentations['false_alarm_rate'] = session.stimulus_presentations['non_change_with_lick'].rolling(win_dur,min_periods=1,win_type=win_type).mean().fillna(0)

    # Get Correct Reject Rate, % of non-change flashes without licks
    session.stimulus_presentations['non_change_without_lick'] = [np.nan if (x[0]) else 0 if (x[1]) else 1 for x in zip(session.stimulus_presentations['change'],session.stimulus_presentations['bout_start'])]
    session.stimulus_presentations['correct_reject_rate'] = session.stimulus_presentations['non_change_without_lick'].rolling(win_dur,min_periods=1,win_type=win_type).mean().fillna(0)

    # Get dPrime and Criterion metrics on a flash level
    Z = norm.ppf
    session.stimulus_presentations['d_prime']   = Z(np.clip(session.stimulus_presentations['hit_rate'],0.01,0.99)) - Z(np.clip(session.stimulus_presentations['false_alarm_rate'],0.01,0.99)) 
    session.stimulus_presentations['criterion'] = 0.5*(Z(np.clip(session.stimulus_presentations['hit_rate'],0.01,0.99)) + Z(np.clip(session.stimulus_presentations['false_alarm_rate'],0.01,0.99)))
        # Computing the criterion to be negative
 
def classify_by_flash_metrics(session, lick_threshold = 0.1, reward_threshold=2/80,use_bouts=True):
    '''
        Use the flash level rolling metrics to classify into three states based on the thresholds
        lick_threshold is the licking rate / flash that divides high and low licking states
        reward_threshold is the rewards/flash that divides high and low reward states (2/80 is equivalent to 2 rewards/minute). 
    '''
    if use_bouts:
        session.stimulus_presentations['high_lick'] = [True if x > lick_threshold else False for x in session.stimulus_presentations['bout_rate']] 
    else:
        session.stimulus_presentations['high_lick'] = [True if x > lick_threshold else False for x in session.stimulus_presentations['lick_rate']] 
    session.stimulus_presentations['high_reward'] = [True if x > reward_threshold else False for x in session.stimulus_presentations['reward_rate']] 
    session.stimulus_presentations['flash_metrics_epochs'] = [0 if (not x[0]) & (not x[1]) else 1 if x[1] else 2 for x in zip(session.stimulus_presentations['high_lick'], session.stimulus_presentations['high_reward'])]
    session.stimulus_presentations['flash_metrics_labels'] = ['low-lick,low-reward' if x==0  else 'high-lick,high-reward' if x==1 else 'high-lick,low-reward' for x in session.stimulus_presentations['flash_metrics_epochs']]

def get_engagement_for_fit(fit, lick_threshold=0.1, reward_threshold=2/80, use_bouts=True,win_dur=320, win_type='triang'):
    fit['psydata']['full_df']['bout_rate'] = fit['psydata']['full_df']['bout_start'].rolling(win_dur,min_periods=1, win_type=win_type).mean()/.75
    fit['psydata']['full_df']['high_lick'] = [True if x > lick_threshold else False for x in fit['psydata']['full_df']['bout_rate']] 
    fit['psydata']['full_df']['reward_rate'] = fit['psydata']['full_df']['hits'].rolling(win_dur,min_periods=1,win_type=win_type).mean()/.75
    fit['psydata']['full_df']['high_reward'] = [True if x > reward_threshold else False for x in fit['psydata']['full_df']['reward_rate']] 
    fit['psydata']['full_df']['flash_metrics_epochs'] = [0 if (not x[0]) & (not x[1]) else 1 if x[1] else 2 for x in zip(fit['psydata']['full_df']['high_lick'], fit['psydata']['full_df']['high_reward'])]
    fit['psydata']['full_df']['flash_metrics_labels'] = ['low-lick,low-reward' if x==0  else 'high-lick,high-reward' if x==1 else 'high-lick,low-reward' for x in fit['psydata']['full_df']['flash_metrics_epochs']]
    fit['psydata']['full_df']['engaged'] = [(x=='high-lick,low-reward') or (x=='high-lick,high-reward') for x in fit['psydata']['full_df']['flash_metrics_labels']]
    return fit


'''
Functions below here are for plotting and analysis, not computation

The first set of functions is for single session analysis

'''
def plot_all_metrics(manifest,verbose=False):
    # make session plots for all sessions
    ids = manifest.query('active')['behavior_session_id'].values
    num_crashed =0
    for id in tqdm(ids):
        try:
            filename = MODEL_FREE_DIR+'session_figures/'+str(id)
            session = pgt.get_data(id)
            get_metrics(session)
            plot_metrics(session,filename=filename+'_metrics')
            plt.close('all')
        except:
            num_crashed += 1
            if verbose:
                print(f"{id} crash")
    print(str(num_crashed) +' sessions crashed')
    print(str(len(ids) - num_crashed) + ' sessions saved')

def plot_metrics(session,use_bouts=True,filename=None):
    '''
        plot the lick and reward rates for this session with the classified epochs
        over the course of the session
    '''
    fig,ax = plt.subplots(nrows=3,ncols=1,figsize=(10,8))
    if 'bout_rate' not in session.stimulus_presentations:
        annotate_flash_rolling_metrics(session)
        classify_by_flash_metrics(session)
    
    cluster_labels = session.stimulus_presentations['flash_metrics_epochs'].values
    cluster_colors = sns.color_palette(n_colors=3)
    cluster_colors = np.vstack([cluster_colors[1], cluster_colors[0],cluster_colors[2]])
    cp = np.where(~(np.diff(cluster_labels) == 0))[0]
    cp = np.concatenate([[0], cp, [len(cluster_labels)]])
    plotted = np.zeros(3,)
    labels = ['low-lick, low-reward','high-lick, high-reward','high-lick, low-reward']
    for i in range(0, len(cp)-1):
        if plotted[cluster_labels[cp[i]+1]]:
            ax[0].axvspan(cp[i],cp[i+1],color=cluster_colors[cluster_labels[cp[i]+1]], alpha=0.2)
        else:
            plotted[cluster_labels[cp[i]+1]] = True
            ax[0].axvspan(cp[i],cp[i+1],color=cluster_colors[cluster_labels[cp[i]+1]], alpha=0.2,label=labels[cluster_labels[cp[i]+1]])

    ax[0].plot(session.stimulus_presentations.reward_rate,'m',label='Reward Rate')
    ax[0].axhline(2/80,linestyle='--',alpha=0.5,color='m',label='Reward Threshold')
    if use_bouts:
        ax[0].plot(session.stimulus_presentations.bout_rate,'g',label='Lick Rate')
    else:
        ax[0].plot(session.stimulus_presentations.lick_rate,'g',label='Flash Lick')
    ax[0].axhline(.1,linestyle='--',alpha=0.5,color='g',label='Lick Threshold')
    ax[0].set_xlabel('Flash #',fontsize=16)
    ax[0].set_ylabel('Rate/Flash',fontsize=16)
    ax[0].tick_params(axis='both',labelsize=12)
    ax[0].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[0].set_xlim([0,len(session.stimulus_presentations)])
    ax[0].set_ylim([0,1])


    ax[1].plot(session.stimulus_presentations.bout_rate,'g',label='Lick Rate')
    ax[1].plot(session.stimulus_presentations.hit_fraction,'b',label='Lick Hit Fraction')
    ax[1].plot(session.stimulus_presentations.hit_rate,'r',label='Hit Rate')
    ax[1].plot(session.stimulus_presentations.false_alarm_rate,'k',label='False Alarm')
    ax[1].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[1].set_xlim([0,len(session.stimulus_presentations)])
    ax[1].set_ylim([0,1])
    ax[1].set_xlabel('Flash #',fontsize=16)
    ax[1].set_ylabel('Rate',fontsize=16)
    ax[1].tick_params(axis='both',labelsize=12)

    ax[2].plot(session.stimulus_presentations.d_prime,'k',label='d prime')
    ax[2].plot(session.stimulus_presentations.criterion,'r',label='criterion')
    ax[2].axhline(0,linestyle='--',alpha=0.5,color='k')
    ax[2].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[2].set_xlim([0,len(session.stimulus_presentations)])
    ax[2].set_ylim(bottom=-1)
    ax[2].set_xlabel('Flash #',fontsize=16)
    ax[2].set_ylabel('d prime',fontsize=16)
    ax[2].tick_params(axis='both',labelsize=12)

    plt.tight_layout()   
    if type(filename) is not None:
        plt.savefig(filename+".png")
 
# UPDATE REQUIRED
def plot_2D(session,lick_threshold = 0.1, reward_threshold = 2/80,filename=None):
    '''
        plot the lick and reward rates for this session with the classified epochs
        in 2D space
    '''
    plt.figure()
    if 'bout_rate' not in session.stimulus_presentations:
        annotate_flash_rolling_metrics(session)  
    cluster_colors = sns.color_palette(n_colors=3)  
    cluster_colors = np.vstack([cluster_colors[1], cluster_colors[0],cluster_colors[2]])
    patch1 = patches.Rectangle((0,0),reward_threshold,lick_threshold,edgecolor=cluster_colors[0],facecolor=cluster_colors[0],alpha=0.2)
    plt.gca().add_patch(patch1)
    patch2 = patches.Rectangle((reward_threshold,0),0.05,1,edgecolor=cluster_colors[1],facecolor=cluster_colors[1],alpha=0.2)
    plt.gca().add_patch(patch2)
    patch3 = patches.Rectangle((0,lick_threshold),reward_threshold,1,edgecolor=cluster_colors[2],facecolor=cluster_colors[2],alpha=0.2)
    plt.gca().add_patch(patch3)
    plt.plot(session.stimulus_presentations.reward_rate, session.stimulus_presentations.bout_rate,'ko',alpha=.1)
    plt.ylim([0, 0.4])
    plt.plot([0,reward_threshold],[lick_threshold, lick_threshold],linestyle='--',color='r',alpha=0.5)
    plt.axvline(reward_threshold,linestyle='--',color='r',alpha=0.5)
    plt.xlim(xmin=0)
    plt.ylabel('lick rate/flash')
    plt.xlabel('reward rate/flash')
    if type(filename) is not type(None):
        plt.savefig(filename+".png")

# UPDATE REQUIRED    
def get_time_in_epochs(session):
    '''
        Computes the duration, in seconds, of each epoch in this session
        Returns a tuple (low-lick\low-reward, high-lick\high-reward, high-lick\low-reward)
    '''
    x0 = np.sum(session.stimulus_presentations.flash_metrics_epochs == 0) 
    x1 = np.sum(session.stimulus_presentations.flash_metrics_epochs == 1)
    x2 = np.sum(session.stimulus_presentations.flash_metrics_epochs == 2) 
    times = np.array([x0,x1,x2])*0.75    
    return times

'''
    Functions below here are for population analysis
'''
def plot_rates_summary(df,group=None):
    plot_rates(df, ['d_prime'],group=group)
    plot_rates(df, ['hit_rate'],group=group)
    plot_rates(df, ['fa_rate'],group=group)
    plot_rates(df, ['lick_bout_rate'],group=group)
    plot_rates(df, ['criterion'],group=group)
    plot_rates(df, ['reward_rate'],group=group)
    plot_rates(df, ['engaged'],group=group)
    plot_rates(df, ['hit_fraction'],group=group)
    plot_rates(df, ['d_prime','criterion'], group=group,label='d_prime_and_criterion')
    plot_rates(df, ['lick_bout_rate','reward_rate'], group=group,label='lick_rate')
    plot_rates(df, ['hit_rate','fa_rate'], group=group,label='hit_rates')
    plot_rates(df, ['low_lick_low_reward','high_lick_high_reward','high_lick_low_reward'],group=group, label='state')

def plot_counts_summary(df,group=None):
    plot_counts(df,'num_hits',group=group,ylim=(0,None))
    plot_counts(df,'num_trials',group=group,ylim=(0,None))
    plot_counts(df, 'fraction_engaged', group=group,ylim=(0,1))
    plot_counts(df, 'fraction_low_lick_low_reward', group=group,ylim=(0,1))
    plot_counts(df, 'fraction_high_lick_low_reward', group=group,ylim=(0,1))
    plot_counts(df, 'fraction_high_lick_high_reward', group=group,ylim=(0,1))

def get_colors():
    colors = {
        'd_prime':'r',
        'hit_rate':'g',
        'fa_rate':'k',
        'lick_bout_rate':'r',
        'criterion':'b',
        'reward_rate':'r',
        'engaged':'r',
        'hit_fraction':'r',
        'low_lick_low_reward':'b',
        'high_lick_low_reward':'r',
        'high_lick_high_reward':'g',
        'Sst-IRES-Cre' : (158/255,218/255,229/255),
        'Vip-IRES-Cre' : (197/255,176/255,213/255),
        'Slc17a7-IRES2-Cre' : (255/255,152/255,150/255),
         'OPHYS_1_images_A':(148/255,29/255,39/255),
         'OPHYS_2_images_A':(222/255,73/255,70/255),
         'OPHYS_3_images_A':(239/255,169/255,150/255),
         'OPHYS_4_images_A':(43/255,80/255,144/255),
         'OPHYS_5_images_A':(100/255,152/255,193/255),
         'OPHYS_6_images_A':(195/255,216/255,232/255),
         'OPHYS_1_images_B':(148/255,29/255,39/255),
         'OPHYS_2_images_B':(222/255,73/255,70/255),
         'OPHYS_3_images_B':(239/255,169/255,150/255),
         'OPHYS_4_images_B':(43/255,80/255,144/255),
         'OPHYS_5_images_B':(100/255,152/255,193/255),
         'OPHYS_6_images_B':(195/255,216/255,232/255),
         'F1':(148/255,29/255,39/255),
         'F2':(222/255,73/255,70/255),
         'F3':(239/255,169/255,150/255),
         'N1':(43/255,80/255,144/255),
         'N2':(100/255,152/255,193/255),
         'N3':(195/255,216/255,232/255)

    }
    return colors

def get_clean_session_type():
    type_dict = {
        'OPHYS_1_images_A':'F1',
        'OPHYS_2_images_A':'F2',
        'OPHYS_3_images_A':'F3',
        'OPHYS_4_images_A':'N1',
        'OPHYS_5_images_A':'N2',
        'OPHYS_6_images_A':'N3',
        'OPHYS_1_images_B':'F1',
        'OPHYS_2_images_B':'F2',
        'OPHYS_3_images_B':'F3',
        'OPHYS_4_images_B':'N1',
        'OPHYS_5_images_B':'N2',
        'OPHYS_6_images_B':'N3'    
    }
    return type_dict

# def project_colors():
#     '''
#         Defines a color scheme for various conditions
#     '''
#     tab20= plt.get_cmap("tab20c")
#     colors = {
#         'Sst-IRES-Cre':(158/255,218/255,229/255),
#         'sst':(158/255,218/255,229/255),
#         'Slc17a7-IRES2-Cre':(255/255,152/255,150/255),
#         'slc':(255/255,152/255,150/255),
#         'Vip-IRES-Cre':(197/255,176/255,213/255),
#         'vip':(197/255,176/255,213/255),
#         '1':(148/255,29/255,39/255),
#         '2':(222/255,73/255,70/255),
#         '3':(239/255,169/255,150/255),
#         '4':(43/255,80/255,144/255),
#         '5':(100/255,152/255,193/255),
#         '6':(195/255,216/255,232/255),
#         '1.0':(148/255,29/255,39/255),
#         '2.0':(222/255,73/255,70/255),
#         '3.0':(239/255,169/255,150/255),
#         '4.0':(43/255,80/255,144/255),
#         '5.0':(100/255,152/255,193/255),
#         '6.0':(195/255,216/255,232/255),
#         'active':(.8,.8,.8),
#         'passive':(.4,.4,.4),
#         'familiar':(222/255,73/255,70/255),
#         'novel':(100/255,152/255,193/255),
#         'deep':'r',
#         'shallow':'b',
#         'VISp':'C0',
#         'V1':'C0',
#         'VISl':'C1',
#         'LM':'C1',
#         'VISal':'C2',
#         'AL':'C2',
#         'VISam':'C3',
#         'AM':'C3',
#         'Full': (.7,.7,.7),
#         'visual':tab20(0), 
#         'all-images':tab20(1),
#         'expectation':tab20(2),
#         'behavioral':tab20(8), 
#         'licking':tab20(9),
#         'pupil_and_running':tab20(10),
#         'face_motion_energy':tab20(11),
#         'cognitive':tab20(5), 
#         'task':tab20(6),
#         'beh_model':tab20(7),
#         'behavioral_model':tab20(7),
#         'licks':color_interpolate(tab20(9),tab20(11),6,1),
#         'pupil':color_interpolate(tab20(10),tab20(11),5,0),
#         'running':color_interpolate(tab20(10),tab20(11),5,2),
#         'face_motion_PC_0':color_interpolate(tab20(10),tab20(11),5,5),
#         'face_motion_PC_1':color_interpolate(tab20(10),tab20(11),5,6),
#         'face_motion_PC_2':color_interpolate(tab20(10),tab20(11),5,7),
#         'face_motion_PC_3':color_interpolate(tab20(10),tab20(11),5,8),
#         'face_motion_PC_4':color_interpolate(tab20(10),tab20(11),5,9),
#         'hits':color_interpolate(tab20(6),tab20(7),5,0),
#         'misses':color_interpolate(tab20(6),tab20(7),5,1),
#         'passive_change':color_interpolate(tab20(6),tab20(7),5,2), 
#         'correct_rejects':color_interpolate(tab20(6),tab20(7),5,3),
#         'false_alarms':color_interpolate(tab20(6),tab20(7),5,4),
#         'model_bias':color_interpolate(tab20(6),tab20(7),5,5),
#         'model_omissions1':color_interpolate(tab20(6),tab20(7),5,6),
#         'model_task0':color_interpolate(tab20(6),tab20(7),5,7),
#         'model_timing1D':color_interpolate(tab20(6),tab20(7),5,8),
#         'bias strategy':color_interpolate(tab20(6),tab20(7),5,5),
#         'post omission strategy':color_interpolate(tab20(6),tab20(7),5,6),
#         'task strategy':color_interpolate(tab20(6),tab20(7),5,7),
#         'timing strategy':color_interpolate(tab20(6),tab20(7),5,8),
#         'image0':color_interpolate(tab20(1), tab20(3),8,0),
#         'image1':color_interpolate(tab20(1), tab20(3),8,1),
#         'image2':color_interpolate(tab20(1), tab20(3),8,2),
#         'image3':color_interpolate(tab20(1), tab20(3),8,3),
#         'image4':color_interpolate(tab20(1), tab20(3),8,4),
#         'image5':color_interpolate(tab20(1), tab20(3),8,5),
#         'image6':color_interpolate(tab20(1), tab20(3),8,6),
#         'image7':color_interpolate(tab20(1), tab20(3),8,7),
#         'omissions':color_interpolate(tab20(1), tab20(3),8,8)
#         } 
#     return colors
# 
# def color_interpolate(start, end, num,position):
#     diff = (np.array(start) - np.array(end))/num
#     return tuple(start-diff*position)




def get_styles():
    styles = {
        'Sst-IRES-Cre':'--',
        'Vip-IRES-Cre':'-',
        'Slc17a7-IRES2-Cre':'-.',
        'OPHYS_1_images_A':'--',
        'OPHYS_3_images_A':'--',
        'OPHYS_4_images_A':'--',
        'OPHYS_6_images_A':'--',
        'OPHYS_1_images_B':'--',
        'OPHYS_3_images_B':'--',
        'OPHYS_4_images_B':'--',
        'OPHYS_6_images_B':'--'
    }
    return styles

def plot_counts(df, count, group=None, label=None,ylim=None):
    plt.figure(figsize=(5,5))
    colors = get_colors()
    styles = get_styles()
    groups = df[group].unique()
    labels = []
    for index, g in enumerate(groups):
        g_df = df[df[group] == g].copy()
        val = np.nanmean(g_df[count])
        sem = np.std(g_df[count])/np.sqrt(len(g_df))
        plt.plot([index-.5,index+.5], [val, val],'-',linewidth=4,color=colors.get(g,'k'))
        plt.plot([index,index], [val-sem, val+sem],'k-',alpha=.5)
        labels.append(g)
    plt.ylabel(count)
    plt.xlabel(group)
    plt.xticks(np.arange(0,len(groups)), labels)
    if ylim is not None:
        plt.ylim(ylim)
    plt.tight_layout()
    if group is not None:
        group = '_by_'+str(group)
    else:
        group =''
    plt.savefig(MODEL_FREE_DIR+'summary_figures/avg_'+count+group+'.png')
    plt.savefig(MODEL_FREE_DIR+'summary_figures/avg_'+count+group+'.svg')
 
def plot_rates(df, rates, group=None,label=None):
    plt.figure(figsize=(10,5))
    colors = get_colors()
    styles = get_styles()
    if group is not None:
        groups = df[group].unique()
    for rate in rates:
        if group is not None:
            for g in groups:
                g_df = df[df[group] == g].copy()
                plt.plot(np.nanmean(np.vstack(g_df[rate]),axis=0),color=colors[rate],label=g+' '+rate,linestyle=styles[g])
        else:
            plt.plot(np.nanmean(np.vstack(df[rate]),axis=0),color=colors[rate],label=rate)
    if len(rates) ==1:
        label = rates[0]
    plt.ylabel(label,fontsize=16)
    plt.xlabel('Image #',fontsize=16)
    plt.axhline(0, color='k',linestyle='--',alpha=.3)
    plt.xticks(fontsize=14)
    plt.yticks(fontsize=14)
    plt.xlim(0,4800)
    plt.legend()
    plt.tight_layout()
    if group is not None:
        group = '_by_'+str(group)
    else:
        group =''
    plt.savefig(MODEL_FREE_DIR+'summary_figures/avg_'+label+group+'.png')
    plt.savefig(MODEL_FREE_DIR+'summary_figures/avg_'+label+group+'.svg')

# UPDATE REQUIRED
def plot_all_times(times,count,all_times,label):
    plt.figure(figsize=(5,5))
    labels = ['low-lick\nlow-reward','high-lick\nhigh-reward','high-lick\nlow-reward']
    means = np.mean(all_times/np.sum(all_times,1)[:,None],0)*100
    sem = np.std(all_times/np.sum(all_times,1)[:,None],0)/np.sqrt(count)*100
    colors = sns.color_palette(n_colors=3)   
    colors = np.vstack([colors[1], colors[0],colors[2]])
    for i in range(0,3):
        plt.plot([i-.5,i+.5],[means[i],means[i]],'-',color=colors[i],linewidth=4)
        plt.plot([i,i], [means[i]-sem[i], means[i]+sem[i]], 'k-')
    plt.xticks([0,1,2],labels,rotation=90,fontsize=20)
    plt.ylabel('% of time in each epoch',fontsize=20)
    plt.ylim([0,100])
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_times_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_times_'+label+'.png')

# UPDATE REQUIRED
def plot_all_epochs(manifest,label):
    plt.figure(figsize=(10,5))
    colors = sns.color_palette(n_colors=3)  
    colors = np.vstack([colors[1], colors[0],colors[2]])
    labels = ['low-lick, low-reward','high-lick, high-reward','high-lick, low-reward']
    count = np.shape(all_epochs)[0]
    for i in range(0,3):
        plt.plot(np.sum(all_epochs==i,0)/count*100,color=colors[i],label=labels[i]) ## hard coded bug
    
    plt.ylim([0,100])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('% of session in each epoch',fontsize=20)
    plt.xlabel('Flash #',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    #plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_epochs_'+label+'.svg')
    #plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_epochs_'+label+'.png')  

# UPDATE REQUIRED
def plot_all_rates(all_lick,all_reward,label):
    plt.figure(figsize=(10,5))
    colors = sns.color_palette("hls",2)
    labels=['Lick Rate', 'Reward Rate']
    plt.plot(np.nanmean(all_lick,0),color=colors[0], label=labels[0]) 
    plt.plot(np.nanmean(all_reward,0),color=colors[1], label=labels[1]) 

    plt.ylim([0,0.25])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('Rate/Flash',fontsize=20)
    plt.xlabel('Flash #',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_rates_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_rates_'+label+'.png')  

# UPDATE REQUIRED
def plot_all_dprime(all_dprime,criterion,label):
    plt.figure(figsize=(10,5))
    colors = sns.color_palette("hls",2)
    labels=['d prime','criterion']
    plt.plot(np.nanmean(all_dprime,0),color=colors[0], label=labels[0]) 
    plt.plot(np.nanmean(criterion,0),color=colors[1], label=labels[1]) 

    plt.ylim([-3,3])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('d prime',fontsize=20)
    plt.xlabel('Flash #',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_dprime_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_dprime_'+label+'.png')  

# UPDATE REQUIRED
def plot_all_performance_rates(all_hit_fraction,all_hit_rate, all_fa_rate,label):
    plt.figure(figsize=(10,5))
    colors = sns.color_palette("hls",3)
    labels=['Lick Hit Fraction', 'Hit Rate','False Alarm Rate']
    plt.plot(np.nanmean(all_hit_fraction,0),color=colors[0], label=labels[0]) 
    plt.plot(np.nanmean(all_hit_rate,0),color=colors[1], label=labels[1]) 
    plt.plot(np.nanmean(all_fa_rate,0),color=colors[2], label=labels[2]) 
    plt.ylim([0,1])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('Rate',fontsize=20)
    plt.xlabel('Flash #',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_performance_rates_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_performance_rates_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_rates(all_lick,all_reward,rlabels,label):
    plt.figure(figsize=(10,5))

    labels=['Lick Rate', 'Reward Rate']
    if len(all_lick) == 2:  
        colors = sns.color_palette("hls",2)
        plt.plot(np.nanmean(all_lick[0],0),'-',color=colors[0], label=labels[0]+" " + rlabels[0]) 
        plt.plot(np.nanmean(all_reward[0],0),'-',color=colors[1], label=labels[1]+" " + rlabels[0]) 
        plt.plot(np.nanmean(all_lick[1],0),'--',color=colors[0], label=labels[0]+" " + rlabels[1]) 
        plt.plot(np.nanmean(all_reward[1],0),'--',color=colors[1], label=labels[1]+" " + rlabels[1])
        pvals = []
        for i in range(0,4790): 
            temp = ss.ttest_ind(all_lick[0][:,i],all_lick[1][:,i])
            pvals.append(temp.pvalue)
            if temp.pvalue < 0.05:
                plt.plot(i,0.001,'ks')
        pvals = np.array(pvals)
    else:
        colors = sns.color_palette("hls",len(all_lick))
        for i in range(0,len(all_lick)):
            plt.plot(np.nanmean(all_lick[i],0),'-',color=colors[i], label=labels[0]+" " + rlabels[i]) 
            plt.plot(np.nanmean(all_reward[i],0),'--',color=colors[i], label=labels[1]+" " + rlabels[i]) 
    plt.ylim([0,0.25])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('Rate/Flash')
    plt.xlabel('Flash #')
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_rates_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_rates_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_performance_rates(all_hit_fraction,all_hit_rate,all_fa_rate,rlabels,label):
    plt.figure(figsize=(10,5))

    labels=['Lick Hit Fraction','Hit Rate','False Alarm Rate']
    if len(all_hit_fraction) == 2:  
        colors = sns.color_palette("hls",2)
        for i in range(0,len(all_hit_fraction)):
            plt.plot(np.nanmean(all_hit_fraction[i],0),'-',color=colors[i], label=labels[0]+" " + rlabels[i]) 
            plt.plot(np.nanmean(all_hit_rate[i],0),'--',color=colors[i], label=labels[1]+" " + rlabels[i]) 
            plt.plot(np.nanmean(all_fa_rate[i],0),'-.',color=colors[i], label=labels[2]+" " + rlabels[i]) 
        pvals = []
        for i in range(0,4790): 
            temp = ss.ttest_ind(all_hit_fraction[0][:,i],all_hit_fraction[1][:,i])
            pvals.append(temp.pvalue)
            if temp.pvalue < 0.05:
                plt.plot(i,0.001,'ks')
        pvals = np.array(pvals)
        pvals = []
        for i in range(0,4790): 
            temp = ss.ttest_ind(all_hit_rate[0][:,i],all_hit_rate[1][:,i])
            pvals.append(temp.pvalue)
            if temp.pvalue < 0.05:
                plt.plot(i,0.02,'ms')
        pvals = np.array(pvals)

    else:
        colors = sns.color_palette("hls",len(all_hit_fraction))
        for i in range(0,len(all_hit_fraction)):
            plt.plot(np.nanmean(all_hit_fraction[i],0),'-',color=colors[i], label=labels[0]+" " + rlabels[i]) 
            plt.plot(np.nanmean(all_hit_rate[i],0),'--',color=colors[i], label=labels[1]+" " + rlabels[i]) 
            plt.plot(np.nanmean(all_fa_rate[i],0),'-.',color=colors[i], label=labels[2]+" " + rlabels[i]) 
    plt.ylim([0,1])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('Rate')
    plt.xlabel('Flash #')
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_'+label+'.png')  


# UPDATE REQUIRED
def compare_all_dprime(all_dprime,rlabels,label):
    plt.figure(figsize=(10,5))

    labels=['dprime']
    if len(all_dprime) == 2:  
        colors = sns.color_palette("hls",4)
        plt.plot(np.nanmean(all_dprime[0],0),'-',color=colors[0], label=labels[0]+" " + rlabels[0]) 
        plt.plot(np.nanmean(all_dprime[1],0),'--',color=colors[0], label=labels[0]+" " + rlabels[1]) 

        pvals = []
        for i in range(0,4790): 
            temp = ss.ttest_ind(all_dprime[0][:,i],all_dprime[1][:,i])
            pvals.append(temp.pvalue)
            if temp.pvalue < 0.05:
                plt.plot(i,0.001,'ks')
        pvals = np.array(pvals)
    else:
        colors = sns.color_palette("hls",len(all_dprime))
        for i in range(0,len(all_dprime)):
            plt.plot(np.nanmean(all_dprime[i],0),'-',color=colors[i], label=labels[0]+" " + rlabels[i]) 
    plt.ylim(bottom=0)
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('dprime')
    plt.xlabel('Flash #')
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_dprime_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_dprime_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_criterion(criterion,rlabels,label):
    plt.figure(figsize=(10,5))

    labels=['criterion']
    if len(criterion) == 2:  
        colors = sns.color_palette("hls",4)
        plt.plot(np.nanmean(criterion[0],0),'-',color=colors[0], label=labels[0]+" " + rlabels[0]) 
        plt.plot(np.nanmean(criterion[1],0),'--',color=colors[0], label=labels[0]+" " + rlabels[1]) 

        pvals = []
        for i in range(0,4790): 
            temp = ss.ttest_ind(criterion[0][:,i],criterion[1][:,i])
            pvals.append(temp.pvalue)
            if temp.pvalue < 0.05:
                plt.plot(i,0.001,'ks')
        pvals = np.array(pvals)
    else:
        colors = sns.color_palette("hls",len(criterion))
        for i in range(0,len(criterion)):
            plt.plot(np.nanmean(criterion[i],0),'-',color=colors[i], label=labels[0]+" " + rlabels[i]) 
    plt.ylim(-3,3)
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('criterion')
    plt.xlabel('Flash #')
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_criterion_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_criterion_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_epochs(all_epochs,rlabels,label, smoothing=0):
    plt.figure(figsize=(10,5))
    colors = sns.color_palette(n_colors=3)   
    labels = ['low-lick, low-reward','high-lick, high-reward','high-lick, low-reward']
    markers=['-','--','-o','-x']
    for j in range(0,len(all_epochs)):
        count = np.shape(all_epochs[j])[0]
        for i in range(0,3):
            if smoothing > 0:
               plt.plot(mov_avg(np.sum(all_epochs[j]==i,0)/count*100,n=smoothing),markers[j],color=colors[i],label=labels[i]+" "+rlabels[j])        
            else:
               plt.plot(np.sum(all_epochs[j]==i,0)/count*100,markers[j],color=colors[i],label=labels[i]+" "+rlabels[j])    
    plt.ylim([0,100])
    plt.xlim([0,4790])
    plt.legend()
    plt.ylabel('% of session in each epoch')
    plt.xlabel('Flash #')
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_epoch_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_epoch_'+label+'.png')  

# UPDATE REQUIRED
def plot_all_rates_averages(all_lick,all_reward,label):
    plt.figure(figsize=(5,5))
    labels = ['Lick Rate','Reward Rate']
    means = [np.nanmean(all_lick), np.nanmean(all_reward)]
    sem = [np.nanstd(all_lick)/np.sqrt(np.shape(all_lick)[0]), np.nanstd(all_reward)/np.sqrt(np.shape(all_lick)[0])]
    
    colors = sns.color_palette("hls",2)   
    for i in range(0,2):
        plt.plot([i-.5,i+.5],[means[i],means[i]],'-',color=colors[i],linewidth=4)
        plt.plot([i,i], [means[i]-sem[i], means[i]+sem[i]], 'k-')
    plt.xticks([0,1],labels,fontsize=16)
    plt.ylabel('Avg Rate/Flash',fontsize=20)
    plt.ylim([0,.25])
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_rates_averages_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_rates_averages_'+label+'.png')  

# UPDATE REQUIRED
def plot_all_performance_rates_averages(all_dprime,criterion, all_hit_fraction,all_hit_rate,all_fa_rate,label):
    plt.figure(figsize=(5,5))
    labels = ['dprime','criterion','Lick Hit Fraction','Hit Rate','False Alarm Rate']
    means = [np.nanmean(all_dprime),-np.nanmean(criterion), np.nanmean(all_hit_fraction), np.nanmean(all_hit_rate), np.nanmean(all_fa_rate)]
    sem = [np.nanstd(all_dprime)/np.sqrt(np.shape(all_dprime)[0]), np.nanstd(criterion)/np.sqrt(np.shape(criterion)[0]), np.nanstd(all_hit_fraction)/np.sqrt(np.shape(all_hit_fraction)[0]), np.nanstd(all_hit_rate)/np.sqrt(np.shape(all_hit_rate)[0]), np.nanstd(all_fa_rate)/np.sqrt(np.shape(all_fa_rate)[0])]
   
    colors = sns.color_palette("hls",4)   
    for i in range(0,4):
        plt.plot([i-.5,i+.5],[means[i],means[i]],'-',color=colors[i],linewidth=4)
        plt.plot([i,i], [means[i]-sem[i], means[i]+sem[i]], 'k-')
    plt.xticks([0,1,2,3],labels,fontsize=16,rotation=90)
    plt.ylabel('Avg Rate',fontsize=20)
    plt.ylim(bottom=0)
    plt.yticks(fontsize=16)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_performance_rates_averages_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_performance_rates_averages_'+label+'.png')  

# UPDATE REQUIRED
def compare_hit_count(num_hits,labels,label):
    means = []
    sems = []
    for i in range(0,len(num_hits)):
        means.append(np.mean(num_hits[i]))
        sems.append(stats.sem(num_hits[i]))
    
    plt.figure(figsize=(5,5))
    colors = sns.color_palette("hls",len(num_hits))
    w=0.4
    
    ticks = []
    for j in range(0,len(means)):   
        plt.plot([j-w,j+w],[means[j],means[j]],'-',color=colors[j],linewidth=4)
        plt.plot([j,j], [means[j]-sems[j], means[j]+sems[j]], 'k-')
        ticks.append(j)    

    if len(means) == 2:
        ylim = 125
        plt.plot([0,1],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([1,1],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(num_hits[0],num_hits[1])[1] < 0.05:
            plt.plot(.5, ylim*1.1,'k*')
        else:
            plt.text(.5,ylim*1.1, 'ns')

    plt.xticks(ticks,labels,fontsize=12)
    plt.ylabel('Num Hits',fontsize=12)
    plt.ylim(0,150)
    plt.xlim(-0.5,len(means)-.5)
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_hit_count_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_hit_count_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_performance_rates_averages_dprime(all_dprime,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['']
    means=[]
    sems =[]
    maxnum=1
    diffsA = np.nanmean(all_dprime[0][:,0:split_on],1) - np.nanmean(all_dprime[0][:,split_on:],1)
    diffsB = np.nanmean(all_dprime[1][:,0:split_on],1) - np.nanmean(all_dprime[1][:,split_on:],1)

    for i in range(0,len(all_dprime)):    
        if not (type(split_on) == type(None)):
            labels = ['1st half','2nd half'] 
            maxnum=2
            means.append([np.nanmean(all_dprime[i][:,0:split_on]),          np.nanmean(all_dprime[i][:,split_on:]) ])
            sems.append([np.nanstd(all_dprime[i][:,0:split_on])/np.sqrt(np.shape(all_dprime[i][:,0:split_on])[0]), np.nanstd(all_dprime[i][:,split_on:])/np.sqrt(np.shape(all_dprime[i][:,split_on:])[0])])
        else: 
            means.append([np.nanmean(all_dprime[i])])
            sems.append([np.nanstd(all_dprime[i])/np.sqrt(np.shape(all_dprime[i])[0])])

    colors = sns.color_palette("hls",2)
    w = (1/len(all_dprime))/2- .05
    jw = 1/len(all_dprime)
    ldex = []
    lstr = []
    if maxnum == 2:
        colors = np.concatenate([colors, colors])
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])
    if maxnum ==2:
        ylim = 2.5
        plt.plot([0.25,1.25],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0.25,0.25],[ylim, ylim*1.05], 'k-')
        plt.plot([1.25,1.25],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(diffsA,diffsB)[1] < 0.05:
            plt.plot(.75, ylim*1.1,'k*')
        else:
            plt.text(.75,ylim*1.1, 'ns')
    else:
        ylim = 2.5
        plt.plot([0,.5],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([.5,.5],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(np.nanmean(all_dprime[0],1),np.nanmean(all_dprime[1],1))[1] < 0.05:
            plt.plot(.25, ylim*1.1,'k*')
        else:
            plt.text(.25,ylim*1.1, 'ns')

    plt.xticks(ldex,lstr,fontsize=12)
    plt.ylabel('dprime',fontsize=12)
    plt.ylim(0,3)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_dprime_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_dprime_'+label+'.png')  


def compare_all_performance_rates_averages_criterion(criterion,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['']
    means=[]
    sems =[]
    maxnum=1
    diffsA = np.nanmean(criterion[0][:,0:split_on],1) - np.nanmean(criterion[0][:,split_on:],1)
    diffsB = np.nanmean(criterion[1][:,0:split_on],1) - np.nanmean(criterion[1][:,split_on:],1)

    for i in range(0,len(criterion)):    
        if not (type(split_on) == type(None)):
            labels = ['1st half','2nd half'] 
            maxnum=2
            means.append([np.nanmean(criterion[i][:,0:split_on]),          np.nanmean(criterion[i][:,split_on:]) ])
            sems.append([np.nanstd(criterion[i][:,0:split_on])/np.sqrt(np.shape(criterion[i][:,0:split_on])[0]), np.nanstd(criterion[i][:,split_on:])/np.sqrt(np.shape(criterion[i][:,split_on:])[0])])
        else: 
            means.append([np.nanmean(criterion[i])])
            sems.append([np.nanstd(criterion[i])/np.sqrt(np.shape(criterion[i])[0])])

    colors = sns.color_palette("hls",2)
    w = (1/len(criterion))/2- .05
    jw = 1/len(criterion)
    ldex = []
    lstr = []
    if maxnum == 2:
        colors = np.concatenate([colors, colors])
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])
    if maxnum ==2:
        ylim = 2.5
        plt.plot([0.25,1.25],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0.25,0.25],[ylim, ylim*1.05], 'k-')
        plt.plot([1.25,1.25],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(diffsA,diffsB)[1] < 0.05:
            plt.plot(.75, ylim*1.1,'k*')
        else:
            plt.text(.75,ylim*1.1, 'ns')
    else:
        ylim = 2.5
        plt.plot([0,.5],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([.5,.5],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(np.nanmean(criterion[0],1),np.nanmean(criterion[1],1))[1] < 0.05:
            plt.plot(.25, ylim*1.1,'k*')
        else:
            plt.text(.25,ylim*1.1, 'ns')

    plt.xticks(ldex,lstr,fontsize=12)
    plt.ylabel('criterion',fontsize=12)
    plt.ylim(-3,3)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_criterion_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_criterion_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_performance_rates_averages_hit_fraction(hit_fraction,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['']
    means=[]
    sems =[]
    maxnum=1
    diffsA = np.nanmean(hit_fraction[0][:,0:split_on],1) - np.nanmean(hit_fraction[0][:,split_on:],1)
    diffsB = np.nanmean(hit_fraction[1][:,0:split_on],1) - np.nanmean(hit_fraction[1][:,split_on:],1)

    for i in range(0,len(hit_fraction)):    
        if not (type(split_on) == type(None)):
            labels = ['1st half','2nd half'] 
            maxnum=2
            means.append([np.nanmean(hit_fraction[i][:,0:split_on]),          np.nanmean(hit_fraction[i][:,split_on:]) ])
            sems.append([np.nanstd(hit_fraction[i][:,0:split_on])/np.sqrt(np.shape(hit_fraction[i][:,0:split_on])[0]), np.nanstd(hit_fraction[i][:,split_on:])/np.sqrt(np.shape(hit_fraction[i][:,split_on:])[0])])
        else: 
            means.append([np.nanmean(hit_fraction[i])])
            sems.append([np.nanstd(hit_fraction[i])/np.sqrt(np.shape(hit_fraction[i])[0])])

    colors = sns.color_palette("hls",2)
    w = (1/len(hit_fraction))/2- .05
    jw = 1/len(hit_fraction)
    ldex = []
    lstr = []
    if maxnum == 2:
        colors = np.concatenate([colors, colors])
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])
    if maxnum ==2:
        ylim = 0.4
        plt.plot([0.25,1.25],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0.25,0.25],[ylim, ylim*1.05], 'k-')
        plt.plot([1.25,1.25],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(diffsA,diffsB)[1] < 0.05:
            plt.plot(.75, ylim*1.1,'k*')
        else:
            plt.text(.75,ylim*1.1, 'ns')
    else:
        ylim = .4
        plt.plot([0,.5],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([.5,.5],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(np.nanmean(hit_fraction[0],1),np.nanmean(hit_fraction[1],1))[1] < 0.05:
            plt.plot(.25, ylim*1.1,'k*')
        else:
            plt.text(.25,ylim*1.1, 'ns')
    plt.xticks(ldex,lstr,fontsize=12)
    plt.ylabel('hit_fraction',fontsize=12)
    plt.ylim(0,.5)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_hit_fraction_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_hit_fraction_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_performance_rates_averages_hit_rate(hit_rate,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['']
    means=[]
    sems =[]
    maxnum=1
    diffsA = np.nanmean(hit_rate[0][:,0:split_on],1) - np.nanmean(hit_rate[0][:,split_on:],1)
    diffsB = np.nanmean(hit_rate[1][:,0:split_on],1) - np.nanmean(hit_rate[1][:,split_on:],1)

    for i in range(0,len(hit_rate)):    
        if not (type(split_on) == type(None)):
            labels = ['1st half','2nd half'] 
            maxnum=2
            means.append([np.nanmean(hit_rate[i][:,0:split_on]),          np.nanmean(hit_rate[i][:,split_on:]) ])
            sems.append([np.nanstd(hit_rate[i][:,0:split_on])/np.sqrt(np.shape(hit_rate[i][:,0:split_on])[0]), np.nanstd(hit_rate[i][:,split_on:])/np.sqrt(np.shape(hit_rate[i][:,split_on:])[0])])
        else: 
            means.append([np.nanmean(hit_rate[i])])
            sems.append([np.nanstd(hit_rate[i])/np.sqrt(np.shape(hit_rate[i])[0])])

    colors = sns.color_palette("hls",2)
    w = (1/len(hit_rate))/2- .05
    jw = 1/len(hit_rate)
    ldex = []
    lstr = []
    if maxnum == 2:
        colors = np.concatenate([colors, colors])
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])
    if maxnum ==2:
        ylim = .8
        plt.plot([0.25,1.25],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0.25,0.25],[ylim, ylim*1.05], 'k-')
        plt.plot([1.25,1.25],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(diffsA,diffsB)[1] < 0.05:
            plt.plot(.75, ylim*1.1,'k*')
        else:
            plt.text(.75,ylim*1.1, 'ns')
    else:
        ylim = .8
        plt.plot([0,.5],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([.5,.5],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(np.nanmean(hit_rate[0],1),np.nanmean(hit_rate[1],1))[1] < 0.05:
            plt.plot(.25, ylim*1.1,'k*')
        else:
            plt.text(.25,ylim*1.1, 'ns')

    plt.xticks(ldex,lstr,fontsize=12)
    plt.ylabel('hit_rate',fontsize=12)
    plt.ylim(0,1)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_hit_rate_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_hit_rate_'+label+'.png')  





# UPDATE REQUIRED
def compare_all_performance_rates_averages_false_alarm(false_alarm,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['']
    means=[]
    sems =[]
    maxnum=1
    diffsA = np.nanmean(false_alarm[0][:,0:split_on],1) - np.nanmean(false_alarm[0][:,split_on:],1)
    diffsB = np.nanmean(false_alarm[1][:,0:split_on],1) - np.nanmean(false_alarm[1][:,split_on:],1)

    for i in range(0,len(false_alarm)):    
        if not (type(split_on) == type(None)):
            labels = ['1st half','2nd half'] 
            maxnum=2
            means.append([np.nanmean(false_alarm[i][:,0:split_on]),          np.nanmean(false_alarm[i][:,split_on:]) ])
            sems.append([np.nanstd(false_alarm[i][:,0:split_on])/np.sqrt(np.shape(false_alarm[i][:,0:split_on])[0]), np.nanstd(false_alarm[i][:,split_on:])/np.sqrt(np.shape(false_alarm[i][:,split_on:])[0])])
        else: 
            means.append([np.nanmean(false_alarm[i])])
            sems.append([np.nanstd(false_alarm[i])/np.sqrt(np.shape(false_alarm[i])[0])])

    colors = sns.color_palette("hls",2)
    w = (1/len(false_alarm))/2- .05
    jw = 1/len(false_alarm)
    ldex = []
    lstr = []
    if maxnum == 2:
        colors = np.concatenate([colors, colors])
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])
    if maxnum ==2:
        ylim = .4
        plt.plot([0.25,1.25],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0.25,0.25],[ylim, ylim*1.05], 'k-')
        plt.plot([1.25,1.25],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(diffsA,diffsB)[1] < 0.05:
            plt.plot(.75, ylim*1.1,'k*')
        else:
            plt.text(.75,ylim*1.1, 'ns')
    else:
        ylim = .4
        plt.plot([0,.5],[ylim*1.05, ylim*1.05],'k-')
        plt.plot([0,0],[ylim, ylim*1.05], 'k-')
        plt.plot([.5,.5],[ylim, ylim*1.05], 'k-')
        if stats.ttest_ind(np.nanmean(false_alarm[0],1),np.nanmean(false_alarm[1],1))[1] < 0.05:
            plt.plot(.25, ylim*1.1,'k*')
        else:
            plt.text(.25,ylim*1.1, 'ns')

    plt.xticks(ldex,lstr,fontsize=12)
    plt.ylabel('false_alarm',fontsize=12)
    plt.ylim(0,.5)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_false_alarm_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_false_alarm_'+label+'.png')  




# UPDATE REQUIRED
def compare_all_performance_rates_averages(all_dprime,criterion,all_hit_fraction,all_hit_rate,all_fa_rate,rlabels,label,split_on=None,color_alt=False):
    plt.figure(figsize=(5,5))
    labels = ['dprime','criterion','Lick Hit Fraction','Hit Rate','False Alarm Rate']
    means=[]
    sems =[]
    maxnum=5
    for i in range(0,len(all_dprime)):    
        if not (type(split_on) == type(None)):
            labels = ['dprime 1st','dprime 2nd','criterion 1st','criterion 2nd','Lick Hit Fraction 1st','Lick Hit Fraction 2nd','Hit Rate 1st','Hit Rate 2nd', 'False Alarm Rate 1st', 'False Alarm Rate 2nd'] 
            maxnum=10
            means.append([
np.nanmean(all_dprime[i][:,0:split_on]),          np.nanmean(all_dprime[i][:,split_on:]), 
-np.nanmean(criterion[i][:,0:split_on]),           -np.nanmean(criterion[i][:,split_on:]), 
np.nanmean(all_hit_fraction[i][:,0:split_on]),    np.nanmean(all_hit_fraction[i][:,split_on:]), 
np.nanmean(all_hit_rate[i][:,0:split_on]),        np.nanmean(all_hit_rate[i][:,split_on:]), 
np.nanmean(all_fa_rate[i][:,0:split_on]),         np.nanmean(all_fa_rate[i][:,split_on:])
])
            sems.append([
np.nanstd(all_dprime[i][:,0:split_on])/np.sqrt(np.shape(all_dprime[i][:,0:split_on])[0]), np.nanstd(all_dprime[i][:,split_on:])/np.sqrt(np.shape(all_dprime[i][:,split_on:])[0]),
np.nanstd(criterion[i][:,0:split_on])/np.sqrt(np.shape(criterion[i][:,0:split_on])[0]), np.nanstd(criterion[i][:,split_on:])/np.sqrt(np.shape(criterion[i][:,split_on:])[0]), 
np.nanstd(all_hit_fraction[i][:,0:split_on])/np.sqrt(np.shape(all_hit_fraction[i][:,0:split_on])[0]), np.nanstd(all_hit_fraction[i][:,split_on:])/np.sqrt(np.shape(all_hit_fraction[i][:,split_on:])[0]), 
np.nanstd(all_hit_rate[i][:,0:split_on])/np.sqrt(np.shape(all_hit_rate[i][:,0:split_on])[0]), np.nanstd(all_hit_rate[i][:,split_on:])/np.sqrt(np.shape(all_hit_rate[i][:,split_on:])[0]), 
np.nanstd(all_fa_rate[i][:,0:split_on])/np.sqrt(np.shape(all_fa_rate[i][:,0:split_on])[0]), np.nanstd(all_fa_rate[i][:,split_on:])/np.sqrt(np.shape(all_fa_rate[i][:,split_on:])[0])
])
        else: 
            means.append([np.nanmean(all_dprime[i]),-np.nanmean(criterion[i]), np.nanmean(all_hit_fraction[i]), np.nanmean(all_hit_rate[i]), np.nanmean(all_fa_rate[i])])
            sems.append([np.nanstd(all_dprime[i])/np.sqrt(np.shape(all_dprime[i])[0]), np.nanstd(criterion[i])/np.sqrt(np.shape(criterion[i])[0]), np.nanstd(all_hit_fraction[i])/np.sqrt(np.shape(all_hit_fraction[i])[0]), np.nanstd(all_hit_rate[i])/np.sqrt(np.shape(all_hit_rate[i])[0]), np.nanstd(all_fa_rate[i])/np.sqrt(np.shape(all_fa_rate[i])[0])])

    colors = sns.color_palette("hls",5)
    w = (1/len(all_dprime))/2- .05
    jw = 1/len(all_dprime)
    ldex = []
    lstr = []
    if maxnum == 10:
        colors = np.repeat(np.vstack(colors),2,axis=0)
        
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            if color_alt:
                plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[j],linewidth=4)
            else:
                plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[i],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])

    plt.xticks(ldex,lstr,rotation=90)
    plt.ylabel('Avg Rate')
    plt.ylim(bottom=0)
    plt.tight_layout() 
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_performance_rates_averages_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_rates_averages(all_lick,all_reward,rlabels,label,split_on=None):
    plt.figure(figsize=(5,5))
    labels = ['Lick Rate','Reward Rate']
    means=[]
    sems =[]
    maxnum=2
    for i in range(0,len(all_lick)):
        if not (type(split_on) == type(None)):
            labels =  ['Lick Rate 1st','Lick Rate 2nd','Reward Rate 1st', 'Reward Rate 2nd']
            maxnum=4
            means.append([
np.nanmean(all_lick[i][:,0:split_on]),np.nanmean(all_lick[i][:,split_on:]), 
np.nanmean(all_reward[i][:,0:split_on]), np.nanmean(all_reward[i][:,split_on:])])
            sems.append([
np.nanstd(all_lick[i][:,0:split_on])/np.sqrt(np.shape(all_lick[i][:,0:split_on])[0]), np.nanstd(all_lick[i][:,split_on:])/np.sqrt(np.shape(all_lick[i][:,split_on:])[0]), 
np.nanstd(all_reward[i][:,0:split_on])/np.sqrt(np.shape(all_lick[i][:,0:split_on])[0]), np.nanstd(all_reward[i][:,split_on:])/np.sqrt(np.shape(all_lick[i][:,split_on:])[0])
])
        else:
            means.append([np.nanmean(all_lick[i]), np.nanmean(all_reward[i])])
            sems.append([np.nanstd(all_lick[i])/np.sqrt(np.shape(all_lick[i])[0]), np.nanstd(all_reward[i])/np.sqrt(np.shape(all_lick[i])[0])])
    
    colors = sns.color_palette("hls",2)
    if maxnum == 4:
        colors = np.repeat(np.vstack(colors),2,axis=0)
    w = (1/len(all_lick))/2- .05
    jw = 1/len(all_lick)
    ldex = []
    lstr = []
    for j in range(0,len(means)):   
        for i in range(0,maxnum):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[i],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])

    plt.xticks(ldex,lstr,rotation=90)
    plt.ylabel('Avg Rate/Flash')
    plt.ylim([0,.25])
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_rates_averages_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_rates_averages_'+label+'.png')  

# UPDATE REQUIRED
def compare_all_times(times,count,all_times,rlabels,label):
    plt.figure(figsize=(5,5))
    labels = ['low-lick\nlow-reward','high-lick\nhigh-reward','high-lick\nlow-reward']
    means=[]
    sems = []
    for i in range(0,len(times)):
        means.append(np.mean(all_times[i]/np.sum(all_times[i],1)[:,None],0)*100)
        sems.append(np.std(all_times[i]/np.sum(all_times[i],1)[:,None],0)/np.sqrt(count[i])*100)
    colors = sns.color_palette(n_colors=3)  
    w = (1/len(all_times))/2- .05
    jw = 1/len(all_times)
    ldex = []
    lstr = []
    for j in range(0,len(all_times)):
        for i in range(0,3):
            plt.plot([i+jw*j-w,i+jw*j+w],[means[j][i],means[j][i]],'-',color=colors[i],linewidth=4)
            plt.plot([i+jw*j,i+jw*j], [means[j][i]-sems[j][i], means[j][i]+sems[j][i]], 'k-')
            ldex.append(i+jw*j)
            lstr.append(labels[i]+" "+rlabels[j])           
    plt.xticks(ldex,lstr,rotation=90)
    plt.ylabel('% of time in each epoch')
    plt.ylim([0,100])
    plt.tight_layout()
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_times_'+label+'.svg')
    plt.savefig('/home/alex.piet/codebase/behavior/model_free/all_compare_times_'+label+'.png')  


def build_metrics_df(TRAIN=False):
    if TRAIN:
        manifest = pgt.get_training_manifest()
    else:
        manifest = pgt.get_ophys_manifest()
    manifest = manifest.query('active').copy()

    # Add columns
    crashed = 0
    manifest['metrics_available'] = manifest['active'] # copying size
    columns = {'lick_bout_rate','reward_rate','hit_rate','hit_fraction','fa_rate','d_prime','criterion','flash_metrics_epochs','engaged',} 
    for column in columns:
        manifest[column] = [[]]*len(manifest)
    for index, row in tqdm(manifest.iterrows(), total = manifest.shape[0]):
        try:
            session = pgt.get_data(row.behavior_session_id)
            get_metrics(session)
        except:
            if verbose:
                print(str(row.behavior_session_id) + ' crashed')
            manifest.at[index,'metrics_availabile'] = False
            crashed +=1
        else:
            manifest.at[index,'lick_bout_rate'] = get_clean_rate(session.stimulus_presentations['bout_rate'].values)
            manifest.at[index,'reward_rate']    = get_clean_rate(session.stimulus_presentations['reward_rate'].values)
            manifest.at[index,'hit_rate']       = get_clean_rate(session.stimulus_presentations['hit_rate'].values)
            manifest.at[index,'hit_fraction']   = get_clean_rate(session.stimulus_presentations['hit_fraction'].values) 
            manifest.at[index,'fa_rate']        = get_clean_rate(session.stimulus_presentations['false_alarm_rate'].values)
            manifest.at[index,'d_prime']        = get_clean_rate(session.stimulus_presentations['d_prime'].values)
            manifest.at[index,'criterion']      = get_clean_rate(session.stimulus_presentations['criterion'].values)
            manifest.at[index,'flash_metrics_epochs'] = get_clean_rate(session.stimulus_presentations['flash_metrics_epochs'].values)
            manifest.at[index,'engaged']        = [(x==1) or (x==2) for x in manifest.at[index,'flash_metrics_epochs']]
            manifest.at[index,'num_hits']       = np.sum(session.trials.hit)
            manifest.at[index,'num_trials']     = len(session.trials)
            manifest.at[index,'fraction_low_lick_low_reward']   = np.sum(manifest.at[index,'flash_metrics_epochs'] == 0)/4800
            manifest.at[index,'fraction_high_lick_high_reward'] = np.sum(manifest.at[index,'flash_metrics_epochs'] == 1)/4800    
            manifest.at[index,'fraction_high_lick_low_reward']  = np.sum(manifest.at[index,'flash_metrics_epochs'] == 2)/4800   
            manifest.at[index,'fraction_engaged']               = np.sum(manifest.at[index,'engaged'])/4800

    if TRAIN:
        manifest.to_pickle(MODEL_FREE_DIR+'psy_metrics_manifest_march_2021_release_training.pkl')
    else:
        manifest.to_pickle(MODEL_FREE_DIR+'psy_metrics_manifest_march_2021_release.pkl')   
    return manifest
    
def get_metrics_df(TRAIN=False):
    if TRAIN:
        manifest = pd.read_pickle(MODEL_FREE_DIR+'psy_metrics_manifest_march_2021_release_training.pkl')
    else:
        manifest = pd.read_pickle(MODEL_FREE_DIR+'psy_metrics_manifest_march_2021_release.pkl')       
    manifest['low_lick_low_reward']   = [x ==0 for x in manifest['flash_metrics_epochs']]
    manifest['high_lick_high_reward'] = [x ==1 for x in manifest['flash_metrics_epochs']]
    manifest['high_lick_low_reward']  = [x ==2 for x in manifest['flash_metrics_epochs']]
    type_dict = get_clean_session_type()
    manifest['session_type'] = [type_dict[x] for x in manifest['session_type']]
    return manifest

def get_clean_rate(vector, length=4800):
    if len(vector) >= length:
        return vector[0:length]
    else:
        return np.concatenate([vector, [np.nan]*(length-len(vector))])

# UPDATE REQUIRED
def get_rates_df(manifest):
    raise Exception('outdated')
    print('OUTDATED VERSION')
    manifest = manifest.query('active').copy()
    ids = manifest['behavior_session_id'].values
    all_lick, all_reward,all_epochs, times, count,all_times, all_hit_fraction, all_hit_rate, all_fa_rate, all_dprime, criterion, IDS_out,num_hits = get_rates(ids=ids)

    df = pd.DataFrame()
    df['behavior_session_ids'] = IDS_out
    df['all_lick'] = list(all_lick)
    df['all_reward'] = list(all_reward)
    df['all_epochs'] = list(all_epochs)
    df['all_times'] = list(all_times)
    df['all_hit_fraction'] = list(all_hit_fraction)
    df['all_hit_rate'] = list(all_hit_rate)
    df['all_fa_rate'] = list(all_fa_rate)
    df['all_dprime'] = list(all_dprime)
    df['criterion'] = list(criterion)
    df['num_hits'] = list(num_hits)   
 
    manifest = manifest.set_index('behavior_session_id') 
    #df['image_set'] = [manifest.loc[x].image_set for x in IDS_out]
    df['container_id'] = [manifest.loc[x].container_id for x in IDS_out]
    #df['imaging_depth'] = [manifest.loc[x].imaging_depth for x in IDS_out]
    df['session_type'] = [manifest.loc[x].session_type for x in IDS_out]
    #df['cre_line'] = [manifest.loc[x].cre_line for x in IDS_out]
    #df['trained_A'] = df.session_type.isin(['OPHYS_1_images_A','OPHYS_3_images_A','OPHYS_4_images_B','OPHYS_6_images_B'])
    #df['stage'] = df.session_type.str[6]
    return df, times, count

# UPDATE REQUIRED
def unpack_df(df):
    raise Exception('outdated')
    all_lick  =  np.vstack(df['all_lick'].values)
    all_reward = np.vstack(df['all_reward'].values)
    all_epochs = np.vstack(df['all_epochs'].values)
    all_times = np.vstack(df['all_times'].values)
    all_hit_fraction = np.vstack(df['all_hit_fraction'].values)
    all_hit_rate  =    np.vstack(df['all_hit_rate'].values)
    all_fa_rate =      np.vstack(df['all_fa_rate'].values )
    all_dprime = np.vstack(df['all_dprime'].values)
    criterion = np.vstack(df['criterion'].values)
    IDS_out = df['IDS'].values
    num_hits = df['num_hits'].values
    times = np.sum(np.vstack(df['all_times'].values),0)
    count = len(df)
    return all_lick, all_reward,all_epochs, times, count,all_times, all_hit_fraction, all_hit_rate, all_fa_rate, all_dprime, criterion, IDS_out,num_hits

 # UPDATE REQUIRED   
def query_get_rates_df(df,query):
    raise Exception('outdated')
    fdf = df.query(query)
    return fdf

 # UPDATE REQUIRED
def query_get_rates(df,query):
    raise Exception('outdated')
    return unpack_df(query_get_rates_df(df,query))

# UPDATE REQUIRED
def get_rates(ids):
    '''
        Computes summary info for all sessions in ids
    '''
    raise Exception('outdated')
    lick_rate = []
    reward_rate = []
    epochs = []
    hit_fraction =[]
    hit_rate = []
    fa_rate = []
    dprime=[]
    criterion=[]
    IDS = []
    num_hits = []

    times = np.zeros(3,)
    count = 0
    all_times = []

    for id in tqdm(ids):
        try:
            session = pgt.get_data(id)
            get_metrics(session)

            lick_rate.append(session.stimulus_presentations['bout_rate'].values)
            reward_rate.append(session.stimulus_presentations['reward_rate'].values)
            hit_fraction.append(session.stimulus_presentations['hit_fraction'].values)
            hit_rate.append(session.stimulus_presentations['hit_rate'].values)
            fa_rate.append(session.stimulus_presentations['false_alarm_rate'].values)
            dprime.append(session.stimulus_presentations['d_prime'].values)
            criterion.append(session.stimulus_presentations['criterion'].values)
            epochs.append(session.stimulus_presentations['flash_metrics_epochs'].values)

            my_times = get_time_in_epochs(session)
            times += my_times
            count +=1
            all_times.append(my_times)
            IDS.append(id)
            num_hits.append(np.sum(session.trials.hit)) 
        except:
            print(str(id)+' crash')
    
    lens = [len(x) for x in lick_rate]
    all_lick = np.zeros((len(lick_rate), np.max(lens)))
    all_lick[:] = np.nan
    all_reward = np.zeros((len(lick_rate), np.max(lens)))
    all_reward[:] = np.nan
    all_hit_fraction = np.zeros((len(lick_rate), np.max(lens)))
    all_hit_fraction[:] = np.nan
    all_hit_rate = np.zeros((len(lick_rate), np.max(lens)))
    all_hit_rate[:] = np.nan
    all_fa_rate = np.zeros((len(lick_rate), np.max(lens)))
    all_fa_rate[:] = np.nan
    all_dprime = np.zeros((len(lick_rate), np.max(lens)))
    all_dprime[:] = np.nan
    all_criterion = np.zeros((len(lick_rate), np.max(lens)))
    all_criterion[:] = np.nan

    for i in range(0,len(lick_rate)):
        all_lick[i,0:len(lick_rate[i])] = lick_rate[i]   
        all_reward[i,0:len(reward_rate[i])] = reward_rate[i]   
        all_hit_fraction[i,0:len(hit_fraction[i])] = hit_fraction[i]   
        all_hit_rate[i,0:len(hit_rate[i])] = hit_rate[i]   
        all_fa_rate[i,0:len(fa_rate[i])] = fa_rate[i]   
        all_dprime[i,0:len(dprime[i])] = dprime[i]  
        all_criterion[i,0:len(criterion[i])] = criterion[i]   

    lens = [len(x) for x in epochs]
    all_epochs = np.zeros((len(epochs), np.max(lens)))
    all_epochs[:] = np.nan
    for i in range(0,len(epochs)):
        all_epochs[i,0:len(epochs[i])] = epochs[i]

    all_times = np.vstack(all_times)
    return all_lick, all_reward,all_epochs, times, count,all_times, all_hit_fraction, all_hit_rate, all_fa_rate, all_dprime, all_criterion, IDS,num_hits

# UPDATE REQUIRED
def mov_avg(a,n=5):
    ret = np.cumsum(a, dtype=float)
    ret[n:] = ret[n:] - ret[:-n]
    return ret[n - 1:] / n

# UPDATE REQUIRED
def get_num_hits(ids):
    raise Exception('outdated')
    num_hits = []
    for index, id in enumerate(ids):
        session = pgt.get_data(id)
        num_hits.append(np.sum(session.trials.hit)) 
    return num_hits 
