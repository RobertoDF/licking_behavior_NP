#from datetime import datetime, timedelta
import os
#from os import makedirs
import copy
import pickle
import numpy as np
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt
from psytrack.hyperOpt import hyperOpt
from psytrack.helper.invBlkTriDiag import getCredibleInterval
from psytrack.helper.helperFunctions import read_input
from psytrack.helper.crossValidation import Kfold_crossVal
from psytrack.helper.crossValidation import Kfold_crossVal_check
#from allensdk.internal.api import behavior_lims_api as bla
#from allensdk.internal.api import behavior_ophys_api as boa
from sklearn.linear_model import LinearRegression
from sklearn.linear_model import LogisticRegressionCV as logregcv
from sklearn.linear_model import LogisticRegression as logreg
from sklearn.cluster import k_means
from sklearn.metrics import roc_auc_score
from sklearn.metrics import roc_curve
from sklearn import metrics
from sklearn.decomposition import PCA
#from allensdk.brain_observatory.behavior import behavior_ophys_session as bos
#from allensdk.brain_observatory.behavior import stimulus_processing
#from allensdk.brain_observatory.behavior.behavior_ophys_session import BehaviorOphysSession
#from allensdk.brain_observatory.behavior.behavior_project_cache import BehaviorProjectCache as bpc
#from functools import reduce
import psy_timing_tools as pt
import psy_metrics_tools as pm
import psy_general_tools as pgt
from scipy.optimize import curve_fit
from scipy.stats import ttest_ind
from scipy.stats import ttest_rel
from tqdm import tqdm
#from visual_behavior.translator.allensdk_sessions import sdk_utils

OPHYS=True #if True, loads the data with BehaviorOphysSession, not BehaviorSession
global_directory="/home/alex.piet/codebase/behavior/psy_fits_v10/" # Where to save results

def load(filepath):
    '''
        Handy function for loading a pickle file. 
    '''
    filetemp = open(filepath,'rb')
    data    = pickle.load(filetemp)
    filetemp.close()
    return data

def save(filepath, variables):
    '''
        Handy function for saving variables to a pickle file. 
    '''
    file_temp = open(filepath,'wb')
    pickle.dump(variables, file_temp)
    file_temp.close()
    
def annotate_stimulus_presentations(session,ignore_trial_errors=False):
    '''
        Adds columns to the stimulus_presentation table describing whether certain task events happened during that flash
        Inputs:
        session, the SDK session object
    
        Appends columns:
        licked, True if the mouse licked during this flash, does not care about the response window
        hits,   True if the mouse licked on a change flash. 
        misses, True if the mouse did not lick on a change flash
        aborts, True if the mouse licked on a non-change-flash. THIS IS NOT THE SAME AS THE TRIALS TABLE ABORT DEFINITION.
                licks on sequential flashes that are during the abort time out period are counted as aborts here.
                this abort list should only be used for simple visualization purposes
        in_grace_period, True if this flash occurs during the 0.75 - 4.5 period after the onset of a hit change
        false_alarm,    True if the mouse licked on a sham-change-flash
        correct_reject, True if the mouse did not lick on a sham-change-flash
        auto_rewards,   True if there was an auto-reward during this flash
    '''
    session.stimulus_presentations['licked'] = ~session.stimulus_presentations.licks.str[0].isnull()
    session.stimulus_presentations['hits'] = session.stimulus_presentations['licked'] & session.stimulus_presentations['change']
    session.stimulus_presentations['misses'] = ~session.stimulus_presentations['licked'] & session.stimulus_presentations['change']
    session.stimulus_presentations['aborts'] = session.stimulus_presentations['licked'] & ~session.stimulus_presentations['change']
    session.stimulus_presentations['in_grace_period'] = (session.stimulus_presentations['time_from_last_change'] <= 4.5) & (session.stimulus_presentations['time_from_last_reward'] <=4.5)
    session.stimulus_presentations.at[session.stimulus_presentations['in_grace_period'],'aborts'] = False # Remove Aborts that happened during grace period
    session.stimulus_presentations['false_alarm'] = False
    session.stimulus_presentations['correct_reject'] = False
    session.stimulus_presentations['auto_rewards'] = False

    # These ones require iterating the fucking trials table, and is super slow
    try:
        for i in session.stimulus_presentations.index:
            found_it=True
            trial = session.trials[(session.trials.start_time <= session.stimulus_presentations.at[i,'start_time']) & (session.trials.stop_time >=session.stimulus_presentations.at[i,'start_time'] + 0.25)]
            if len(trial) > 1:
                raise Exception("Could not isolate a trial for this flash")
            if len(trial) == 0:
                trial = session.trials[(session.trials.start_time <= session.stimulus_presentations.at[i,'start_time']) & (session.trials.stop_time+0.75 >= session.stimulus_presentations.at[i,'start_time'] + 0.25)]  
                if ( len(trial) == 0 ) & (session.stimulus_presentations.at[i,'start_time'] > session.trials.start_time.values[-1]):
                    trial = session.trials[session.trials.index == session.trials.index[-1]]
                elif ( len(trial) ==0) & (session.stimulus_presentations.at[i,'start_time'] < session.trials.start_time.values[0]):
                    trial = session.trials[session.trials.index == session.trials.index[0]]
                elif np.sum(session.trials.aborted) == 0:
                    found_it=False
                elif len(trial) == 0:
                    trial = session.trials[(session.trials.start_time <= session.stimulus_presentations.at[i,'start_time']+0.75) & (session.trials.stop_time+0.75 >= session.stimulus_presentations.at[i,'start_time'] + 0.25)]  
                    if len(trial) == 0: 
                        print('stim index: '+str(i))
                        raise Exception("Could not find a trial for this flash")
            if found_it:
                if trial['false_alarm'].values[0]:
                    if (trial.change_time.values[0] >= session.stimulus_presentations.at[i,'start_time']) & (trial.change_time.values[0] <= session.stimulus_presentations.at[i,'stop_time'] ):
                        session.stimulus_presentations.at[i,'false_alarm'] = True
                if trial['correct_reject'].values[0]:
                    if (trial.change_time.values[0] >= session.stimulus_presentations.at[i,'start_time']) & (trial.change_time.values[0] <= session.stimulus_presentations.at[i,'stop_time'] ):
                        session.stimulus_presentations.at[i,'correct_reject'] = True
                if trial['auto_rewarded'].values[0]:
                    if (trial.change_time.values[0] >= session.stimulus_presentations.at[i,'start_time']) & (trial.change_time.values[0] <= session.stimulus_presentations.at[i,'stop_time'] ):
                        session.stimulus_presentations.at[i,'auto_rewards'] = True
    except:
        if ignore_trial_errors:
            print('WARNING, had trial alignment errors, but are ignoring due to ignore_trial_errors=True')
        else:
            raise Exception('Trial Alignment Error. Set ignore_trial_errors=True to ignore. Flash #: '+str(i))

def format_session(session,format_options):
    '''
        Formats the data into the requirements of Psytrack
        ARGS:
            data outputed from SDK
            format_options, a dictionary with keys:
                fit_bouts, if True (Default), then fits to the start of each licking bout, instead of each lick
                timing0/1, if True (Default), then timing is a vector of 0s and 1s, otherwise, is -1/+1
                mean_center, if True, then regressors are mean-centered
                timing_params, [p1,p2] parameters for 1D timing regressor
                timing_params_session, parameters custom fit for this session
                                
        Returns:
            data formated for psytrack. A dictionary with key/values:
            psydata['y'] = a vector of no-licks (1) and licks(2) for each flashes
            psydata['inputs'] = a dictionary with each key an input ('random','timing', 'task', etc)
                each value has a 2D array of shape (N,M), where N is number of flashes, and M is 1 unless you want to look at history/flash interaction terms
    '''     
    if len(session.licks) < 10:
        raise Exception('Less than 10 licks in this session')   

    defaults = {'fit_bouts':True,'timing0/1':True,'mean_center':False,'timing_params':np.array([-5,4]),'timing_params_session':np.array([-5,4]),'ignore_trial_errors':False}
    for k in defaults.keys():
        if k not in format_options:
            format_options[k] = defaults[k]

    # Build Dataframe of flashes
    annotate_stimulus_presentations(session,ignore_trial_errors = format_options['ignore_trial_errors'])
    df = pd.DataFrame(data = session.stimulus_presentations.start_time)
    if format_options['fit_bouts']:
        licks = session.stimulus_presentations.bout_start.values
        df['y'] = np.array([2 if x else 1 for x in licks])
    else:
        licks = session.stimulus_presentations.licks.str[0].isnull()
        df['y'] = np.array([1 if x else 2 for x in licks])
    df['hits'] = session.stimulus_presentations.hits
    df['misses'] = session.stimulus_presentations.misses
    df['false_alarm'] = session.stimulus_presentations.false_alarm
    df['correct_reject'] = session.stimulus_presentations.correct_reject
    df['aborts'] = session.stimulus_presentations.aborts
    df['auto_rewards'] = session.stimulus_presentations.auto_rewards
    df['start_time'] = session.stimulus_presentations.start_time
    df['change'] = session.stimulus_presentations.change
    df['omitted'] = session.stimulus_presentations.omitted  
    df['licked'] = session.stimulus_presentations.licked
    df['included'] = True
 
    # Build Dataframe of regressors
    if format_options['fit_bouts']:
        df['bout_start'] = session.stimulus_presentations['bout_start']
        df['bout_end'] = session.stimulus_presentations['bout_end']
        df['num_bout_start'] = session.stimulus_presentations['num_bout_start']
        df['num_bout_end'] = session.stimulus_presentations['num_bout_end']
        df['flashes_since_last_lick'] = session.stimulus_presentations.groupby(session.stimulus_presentations['bout_end'].cumsum()).cumcount(ascending=True)
        df['in_bout_raw_bad'] = session.stimulus_presentations['bout_start'].cumsum() > session.stimulus_presentations['bout_end'].cumsum()
        df['in_bout_raw'] = session.stimulus_presentations['num_bout_start'].cumsum() > session.stimulus_presentations['num_bout_end'].cumsum()
        df['in_bout'] = np.array([1 if x else 0 for x in df['in_bout_raw'].shift(fill_value=False)])
        df['task0'] = np.array([1 if x else 0 for x in df['change']])
        df['task1'] = np.array([1 if x else -1 for x in df['change']])
        df['taskCR'] = np.array([0 if x else -1 for x in df['change']])
        df['omissions'] = np.array([1 if x else 0 for x in df['omitted']])
        df['omissions1'] = np.array([x for x in np.concatenate([[0], df['omissions'].values[0:-1]])])
        df['timing1D'] = np.array([timing_sigmoid(x,format_options['timing_params']) for x in df['flashes_since_last_lick'].shift()])
        df['timing1D_session'] = np.array([timing_sigmoid(x,format_options['timing_params_session']) for x in df['flashes_since_last_lick'].shift()])
        if format_options['timing0/1']:
            min_timing_val = 0
        else:
            min_timing_val = -1
        df['timing1'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==0])
        df['timing2'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==1])
        df['timing3'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==2])
        df['timing4'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==3])
        df['timing5'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==4])
        df['timing6'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==5])
        df['timing7'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==6])
        df['timing8'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==7])
        df['timing9'] =  np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==8])
        df['timing10'] = np.array([1 if x else min_timing_val for x in df['flashes_since_last_lick'].shift() ==9])
        df['included'] = df['in_bout'] ==0
        full_df = copy.copy(df)
        df = df[df['in_bout']==0] 
        df['missing_trials'] = np.concatenate([np.diff(df.index)-1,[0]])
    else:
        df['task0'] = np.array([1 if x else 0 for x in df['change']])
        df['task1'] = np.array([1 if x else -1 for x in df['change']])
        df['taskCR'] = np.array([0 if x else -1 for x in df['change']])
        df['omissions'] = np.array([1 if x else 0 for x in df['omitted']])
        df['omissions1'] = np.concatenate([[0], df['omissions'].values[0:-1]])
        df['flashes_since_last_lick'] = df.groupby(df['licked'].cumsum()).cumcount(ascending=True)
        if format_options['timing0/1']:
            df['timing2'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=2])
            df['timing3'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=3])
            df['timing4'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=4])
            df['timing5'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=5])
            df['timing6'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=6])
            df['timing7'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=7])
            df['timing8'] = np.array([1 if x else 0 for x in df['flashes_since_last_lick'].shift() >=8]) 
        else:
            df['timing2'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=2])
            df['timing3'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=3])
            df['timing4'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=4])
            df['timing5'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=5])
            df['timing6'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=6])
            df['timing7'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=7])
            df['timing8'] = np.array([1 if x else -1 for x in df['flashes_since_last_lick'].shift() >=8])
        df['missing_trials'] = np.array([ 0 for x in df['change']])
        full_df = copy.copy(df)

    # Package into dictionary for psytrack
    inputDict ={'task0': df['task0'].values[:,np.newaxis],
                'task1': df['task1'].values[:,np.newaxis],
                'taskCR': df['taskCR'].values[:,np.newaxis],
                'omissions' : df['omissions'].values[:,np.newaxis],
                'omissions1' : df['omissions1'].values[:,np.newaxis],
                'timing1': df['timing1'].values[:,np.newaxis],
                'timing2': df['timing2'].values[:,np.newaxis],
                'timing3': df['timing3'].values[:,np.newaxis],
                'timing4': df['timing4'].values[:,np.newaxis],
                'timing5': df['timing5'].values[:,np.newaxis],
                'timing6': df['timing6'].values[:,np.newaxis],
                'timing7': df['timing7'].values[:,np.newaxis],
                'timing8': df['timing8'].values[:,np.newaxis],
                'timing9': df['timing9'].values[:,np.newaxis],
                'timing10': df['timing10'].values[:,np.newaxis],
                'timing1D': df['timing1D'].values[:,np.newaxis],
                'timing1D_session': df['timing1D_session'].values[:,np.newaxis]}
   
    # Mean Center the regressors should you need to 
    if format_options['mean_center']:
        for key in inputDict.keys():
            # mean center
            inputDict[key] = inputDict[key] - np.mean(inputDict[key])
   
    # After Mean centering, include missing trials
    inputDict['missing_trials'] = df['missing_trials'].values[:,np.newaxis]

    psydata = { 'y': df['y'].values, 
                'inputs':inputDict, 
                'false_alarms': df['false_alarm'].values,
                'correct_reject': df['correct_reject'].values,
                'hits': df['hits'].values,
                'misses':df['misses'].values,
                'aborts':df['aborts'].values,
                'auto_rewards':df['auto_rewards'].values,
                'start_times':df['start_time'].values,
                'flash_ids': df.index.values,
                'df':df,
                'full_df':full_df }
    try: 
        psydata['session_label'] = [session.metadata['stage']]
    except:
        psydata['session_label'] = ['Unknown Label']  
    return psydata

def timing_sigmoid(x,params,min_val = -1, max_val = 0,tol=1e-3):
    if np.isnan(x):
        x = 0
    x_corrected = x+1
    y = min_val+(max_val-min_val)/(1+(x_corrected/params[1])**params[0])
    if (y -min_val) < tol:
        y = min_val
    if (max_val - y) < tol:
        y = max_val
    return y
    
def fit_weights(psydata, BIAS=True,TASK0=True, TASK1=False,TASKCR = False, OMISSIONS=False,OMISSIONS1=True,TIMING1=False,TIMING2=False,TIMING3=False, TIMING4=False,TIMING5=False,TIMING6=False,TIMING7=False,TIMING8=False,TIMING9=False,TIMING10=False,TIMING1D=True,TIMING1D_SESSION=False,fit_overnight=False):
    '''
        does weight and hyper-parameter optimization on the data in psydata
        Args: 
            psydata is a dictionary with key/values:
            psydata['y'] = a vector of no-licks (1) and licks(2) for each flashes
            psydata['inputs'] = a dictionary with each key an input ('random','timing', 'task', etc)
                each value has a 2D array of shape (N,M), where N is number of flashes, and M is 1 unless you want to look at history/flash interaction terms

        RETURNS:
        hyp
        evd
        wMode
        hess
    '''
    weights = {}
    if BIAS: weights['bias'] = 1
    if TASK0: weights['task0'] = 1
    if TASK1: weights['task1'] = 1
    if TASKCR: weights['taskCR'] = 1
    if OMISSIONS: weights['omissions'] = 1
    if OMISSIONS1: weights['omissions1'] = 1
    if TIMING1: weights['timing1'] = 1
    if TIMING2: weights['timing2'] = 1
    if TIMING3: weights['timing3'] = 1
    if TIMING4: weights['timing4'] = 1
    if TIMING5: weights['timing5'] = 1
    if TIMING6: weights['timing6'] = 1
    if TIMING7: weights['timing7'] = 1
    if TIMING8: weights['timing8'] = 1
    if TIMING9: weights['timing9'] = 1
    if TIMING10: weights['timing10'] = 1
    if TIMING1D: weights['timing1D'] = 1
    if TIMING1D_SESSION: weights['timing1D_session'] = 1
    print(weights)

    K = np.sum([weights[i] for i in weights.keys()])
    hyper = {'sigInit': 2**4.,
            'sigma':[2**-4.]*K,
            'sigDay': 2**4}
    if fit_overnight:
        optList=['sigma','sigDay']
    else:
        optList=['sigma']
    hyp,evd,wMode,hess =hyperOpt(psydata,hyper,weights, optList)
    credibleInt = getCredibleInterval(hess)
    return hyp, evd, wMode, hess, credibleInt, weights

def compute_ypred(psydata, wMode, weights):
    g = read_input(psydata, weights)
    gw = g*wMode.T
    total_gw = np.sum(g*wMode.T,axis=1)
    pR = 1/(1+np.exp(-total_gw))
    pR_each = 1/(1+np.exp(-gw))
    return pR, pR_each

def inverse_transform(series):
    return -np.log((1/series) - 1)

def transform(series):
    '''
        passes the series through the logistic function
    '''
    return 1/(1+np.exp(-(series)))

def get_weights_list(weights):
    weights_list = []
    for i in sorted(weights.keys()):
        weights_list += [i]*weights[i]
    return weights_list

def clean_weights(weights):
    weight_dict = {
    'bias':'Bias',
    'omissions0':'Omitted',
    'omissions1':'Prev. Omitted',
    'task0':'Task',
    'timing1D':'Timing'}

    clean_weights = []
    for w in weights:
        if w in weight_dict.keys():
            clean_weights.append(weight_dict[w])
        else:
            clean_weights.append(w)
    return clean_weights

def clean_dropout(weights):
    weight_dict = {
    'Bias':'Bias',
    'Omissions':'Omitted',
    'Omissions1':'Prev. Omitted',
    'Task0':'Task',
    'timing1D':'Timing',
    'Full-Task0':'Full Model'}

    clean_weights = []
    for w in weights:
        if w in weight_dict.keys():
            clean_weights.append(weight_dict[w])
        else:
            clean_weights.append(w)
    return clean_weights

def plot_weights(wMode,weights,psydata,errorbar=None, ypred=None,START=0, END=0,remove_consumption=True,validation=True,session_labels=None, seedW = None,ypred_each = None,filename=None,cluster_labels=None,smoothing_size=50,num_clusters=None):
    '''
        Plots the fit results by plotting the weights in linear and probability space. 
    
    '''
    K,N = wMode.shape    
    if START <0: START = 0
    if START > N: raise Exception(" START > N")
    if END <=0: END = N
    if END > N: END = N
    if START >= END: raise Exception("START >= END")

    #weights_list = []
    #for i in sorted(weights.keys()):
    #    weights_list += [i]*weights[i]
    weights_list = get_weights_list(weights)    

    #my_colors=['blue','green','purple','red','coral','pink','yellow','cyan','dodgerblue','peru','black','grey','violet']  
    my_colors = sns.color_palette("hls",len(weights.keys()))
    if 'dayLength' in psydata:
        dayLength = np.concatenate([[0],np.cumsum(psydata['dayLength'])])
    else:
        dayLength = []

    cluster_ax = 3
    if (not (type(ypred) == type(None))) & validation:
        fig,ax = plt.subplots(nrows=4,ncols=1, figsize=(10,10))
        #ax[3].plot(ypred, 'k',alpha=0.3,label='Full Model')
        ax[3].plot(pgt.moving_mean(ypred,smoothing_size), 'k',alpha=0.3,label='Full Model (n='+str(smoothing_size)+ ')')
        if not( type(ypred_each) == type(None)):
            for i in np.arange(0, len(weights_list)):
                ax[3].plot(ypred_each[:,i], linestyle="-", lw=3, alpha = 0.3,color=my_colors[i],label=weights_list[i])        
        ax[3].plot(pgt.moving_mean(psydata['y']-1,smoothing_size), 'b',alpha=0.5,label='data (n='+str(smoothing_size)+ ')')
        ax[3].set_ylim(0,1)
        ax[3].set_ylabel('Lick Prob',fontsize=12)
        ax[3].set_xlabel('Flash #',fontsize=12)
        ax[3].set_xlim(START,END)
        ax[3].legend(loc='center left', bbox_to_anchor=(1, 0.5))
        ax[3].tick_params(axis='both',labelsize=12)
    elif validation:
        fig,ax = plt.subplots(nrows=3,ncols=1, figsize=(10,8))
        cluster_ax = 2
    elif (not (type(cluster_labels) == type(None))):
        fig,ax = plt.subplots(nrows=3,ncols=1, figsize=(10,8))
        cluster_ax = 2
    else:
        fig,ax = plt.subplots(nrows=2,ncols=1, figsize=(10,6)  )
    if (not (type(cluster_labels) == type(None))):
        cp = np.where(~(np.diff(cluster_labels) == 0))[0]
        cp = np.concatenate([[0], cp, [len(cluster_labels)]])
        #cluster_colors = ['r','b','g','c','m','k','y']
        if type(num_clusters) == type(None):
            num_clusters = len(np.unique(cluster_labels))
        cluster_colors = sns.color_palette("hls",num_clusters)
        for i in range(0, len(cp)-1):
            ax[cluster_ax].axvspan(cp[i],cp[i+1],color=cluster_colors[cluster_labels[cp[i]+1]], alpha=0.3)
    for i in np.arange(0, len(weights_list)):
        ax[0].plot(wMode[i,:], linestyle="-", lw=3, color=my_colors[i],label=weights_list[i])        
        ax[0].fill_between(np.arange(len(wMode[i])), wMode[i,:]-2*errorbar[i], 
            wMode[i,:]+2*errorbar[i],facecolor=my_colors[i], alpha=0.1)    
        ax[1].plot(transform(wMode[i,:]), linestyle="-", lw=3, color=my_colors[i],label=weights_list[i])
        ax[1].fill_between(np.arange(len(wMode[i])), transform(wMode[i,:]-2*errorbar[i]), 
            transform(wMode[i,:]+2*errorbar[i]),facecolor=my_colors[i], alpha=0.1)                  
        if not (type(seedW) == type(None)):
            ax[0].plot(seedW[i,:], linestyle="--", lw=2, color=my_colors[i], label= "seed "+weights_list[i])
            ax[1].plot(transform(seedW[i,:]), linestyle="--", lw=2, color=my_colors[i], label= "seed "+weights_list[i])
    ax[0].plot([0,np.shape(wMode)[1]], [0,0], 'k--',alpha=0.2)
    ax[0].set_ylabel('Weight',fontsize=12)
    ax[0].set_xlabel('Flash #',fontsize=12)
    ax[0].set_xlim(START,END)
    ax[0].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[0].tick_params(axis='both',labelsize=12)
    for i in np.arange(0, len(dayLength)-1):
        ax[0].axvline(dayLength[i],color='k',alpha=0.2)
        if not type(session_labels) == type(None):
            ax[0].text(dayLength[i],ax[0].get_ylim()[1], session_labels[i],rotation=25)
    ax[1].set_ylim(0,1)
    ax[1].set_ylabel('Lick Prob',fontsize=12)
    ax[1].set_xlabel('Flash #',fontsize=12)
    ax[1].set_xlim(START,END)
    #ax[1].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[1].tick_params(axis='both',labelsize=12)
    for i in np.arange(0, len(dayLength)-1):
        ax[1].plot([dayLength[i], dayLength[i]],[0,1], 'k-',alpha=0.2)

    if validation:
        #first_start = session.trials.loc[0].start_time
        jitter = 0.025   
        for i in np.arange(0, len(psydata['hits'])):
            if psydata['hits'][i]:
                ax[2].plot(i, 1+np.random.randn()*jitter, 'bo',alpha=0.2)
            elif psydata['misses'][i]:
                ax[2].plot(i, 1.5+np.random.randn()*jitter, 'ro',alpha = 0.2)   
            elif psydata['false_alarms'][i]:
                ax[2].plot(i, 2.5+np.random.randn()*jitter, 'ko',alpha = 0.2)
            elif psydata['correct_reject'][i] & (not psydata['aborts'][i]):
                ax[2].plot(i, 2+np.random.randn()*jitter, 'co',alpha = 0.2)   
            elif psydata['aborts'][i]:
                ax[2].plot(i, 3+np.random.randn()*jitter, 'ko',alpha=0.2)  
            if psydata['auto_rewards'][i] & (not psydata['aborts'][i]):
                ax[2].plot(i, 3.5+np.random.randn()*jitter, 'go',alpha=0.2)    
    
        ax[2].set_yticks([1,1.5,2,2.5,3,3.5])
        ax[2].set_yticklabels(['hits','miss','CR','FA','abort','auto'],{'fontsize':12})
        ax[2].set_xlim(START,END)
        ax[2].set_xlabel('Flash #',fontsize=12)
        ax[2].tick_params(axis='both',labelsize=12)

    plt.tight_layout()
    if not (type(filename) == type(None)):
        plt.savefig(filename+"_weights.png")
    

def check_lick_alignment(session, psydata):
    '''
        Debugging function that plots the licks in psydata against the session objects
    '''
    plt.figure(figsize=(10,5))
    plt.plot(session.stimulus_presentations.start_time.values,psydata['y']-1, 'ko-')
    all_licks = session.licks
    for index, lick in all_licks.iterrows():
        plt.plot([lick.time, lick.time], [0.9, 1.1], 'r')
    plt.xlabel('time (s)')
    for index, row in session.trials.iterrows():
        if row.hit:
            plt.plot(row.change_time, 1.2, 'bo')
        elif row.miss:
            plt.plot(row.change_time, 1.25, 'gx')   
        elif row.false_alarm:
            plt.plot(row.change_time, 1.3, 'ro')
        elif row.correct_reject:
            plt.plot(row.change_time, 1.35, 'cx')   
        elif row.aborted:
            if len(row.lick_times) >= 1:
                plt.plot(row.lick_times[0], 1.4, 'kx')   
            else:  
                plt.plot(row.start_time, 1.4, 'kx')  
        else:
            raise Exception('Trial had no classification')
   
def sample_model(psydata):
    '''
        Samples the model. This function is a bit broken because it uses the original licking times to determine the timing strategies, and not the new licks that have been sampled. But it works fairly well
    '''
    bootdata = copy.copy(psydata)    
    if not ('ypred' in bootdata):
        raise Exception('You need to compute y-prediction first')
    temp = np.random.random(np.shape(bootdata['ypred'])) < bootdata['ypred']
    licks = np.array([2 if x else 1 for x in temp])   
    bootdata['y'] = licks
    return bootdata


def bootstrap_model(psydata, ypred, weights,seedW,plot_this=True):
    '''
        Does one bootstrap of the data and model prediction
    '''
    psydata['ypred'] =ypred
    bootdata = sample_model(psydata)
    bK = np.sum([weights[i] for i in weights.keys()])
    bhyper = {'sigInit': 2**4.,
        'sigma':[2**-4.]*bK,
        'sigDay': 2**4}
    boptList=['sigma']
    bhyp,bevd,bwMode,bhess =hyperOpt(bootdata,bhyper,weights, boptList)
    bcredibleInt = getCredibleInterval(bhess)
    if plot_this:
        plot_weights(bwMode, weights, bootdata, errorbar=bcredibleInt, validation=False,seedW =seedW )
    return (bootdata, bhyp, bevd, bwMode, bhess, bcredibleInt)

def bootstrap(numboots, psydata, ypred, weights, seedW, plot_each=False):
    '''
    Performs a bootstrapping procedure on a fit by sampling the model repeatedly and then fitting the samples 
    '''
    boots = []
    for i in np.arange(0,numboots):
        print(i)
        boot = bootstrap_model(psydata, ypred, weights, seedW,plot_this=plot_each)
        boots.append(boot)
    return boots

def plot_bootstrap(boots, hyp, weights, seedW, credibleInt,filename=None):
    '''
        Calls each of the plotting functions for the weights and the prior
    '''
    plot_bootstrap_recovery_prior(boots,hyp, weights,filename)
    plot_bootstrap_recovery_weights(boots,hyp, weights,seedW,credibleInt,filename)


def plot_bootstrap_recovery_prior(boots,hyp,weights,filename):
    '''
        Plots how well the bootstrapping procedure recovers the hyper-parameter priors. Plots the seed prior and each bootstrapped value
    '''
    fig,ax = plt.subplots(figsize=(3,4))
    #my_colors=['blue','green','purple','red','coral','pink','yellow','cyan','dodgerblue','peru','black','grey','violet']
    my_colors = sns.color_palette("hls",len(weights.keys()))
    plt.yscale('log')
    plt.ylim(0.001, 20)
    ax.set_xticks(np.arange(0,len(hyp['sigma'])))
    #weights_list = []
    #for i in sorted(weights.keys()):
    #    weights_list += [i]*weights[i]
    weights_list = get_weights_list(weights)
    ax.set_xticklabels(weights_list,rotation=90)
    plt.ylabel('Smoothing Prior, $\sigma$ \n <-- More Smooth      More Variable -->')
    for boot in boots:
        plt.plot(boot[1]['sigma'], 'kx',alpha=0.5)
    for i in np.arange(0, len(hyp['sigma'])):
        plt.plot(i,hyp['sigma'][i], 'o', color=my_colors[i])

    plt.tight_layout()
    if not (type(filename) == type(None)):
        plt.savefig(filename+"_bootstrap_prior.png")

def plot_bootstrap_recovery_weights(boots,hyp,weights,wMode,errorbar,filename):
    '''
        plots the output of bootstrapping on the weight trajectories, plots the seed values and each bootstrapped recovered value   
    '''
    fig,ax = plt.subplots( figsize=(10,3.5))
    K,N = wMode.shape
    plt.xlim(0,N)
    plt.xlabel('Flash #',fontsize=12)
    plt.ylabel('Weight',fontsize=12)
    ax.tick_params(axis='both',labelsize=12)

    #my_colors=['blue','green','purple','red','coral','pink','yellow','cyan','dodgerblue','peru','black','grey','violet']
    my_colors = sns.color_palette("hls",len(weights.keys()))
    for i in np.arange(0, K):
        plt.plot(wMode[i,:], "-", lw=3, color=my_colors[i])
        ax.fill_between(np.arange(len(wMode[i])), wMode[i,:]-2*errorbar[i], 
            wMode[i,:]+2*errorbar[i],facecolor=my_colors[i], alpha=0.1)    

        for boot in boots:
            plt.plot(boot[3][i,:], '--', color=my_colors[i], alpha=0.2)
    plt.tight_layout()
    if not (type(filename) == type(None)):
        plt.savefig(filename+"_bootstrap_weights.png")


def dropout_analysis(psydata, BIAS=True,TASK0=True, TASK1=False,TASKCR = False, OMISSIONS=True,OMISSIONS1=True,TIMING1=False, TIMING2=False,TIMING3=False, TIMING4=False,TIMING5=False,TIMING6=False,TIMING7=False,TIMING8=False,TIMING9=False, TIMING10=False,TIMING1D=True,TIMING1D_SESSION=False):
    '''
        Computes a dropout analysis for the data in psydata. In general, computes a full set, and then removes each feature one by one. Also computes hard-coded combinations of features
        Returns a list of models and a list of labels for each dropout
    '''
    models =[]
    labels=[]
    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10,TIMING1D=TIMING1D, TIMING1D_SESSION=TIMING1D_SESSION)
    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    labels.append('Full-Task0')

    if BIAS:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=False, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Bias')
    if TASK0:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=False,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS,  OMISSIONS1=OMISSIONS1, TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Task0')
    if TASK1:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=False, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1,  TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Task1')
    if TASKCR:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=False, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('TaskCR')
    if (TASK0 & TASK1) | (TASK0 & TASKCR) | (TASK1 & TASKCR):
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=False,TASK1=False, TASKCR=False, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('All Task')
    if OMISSIONS:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=False, OMISSIONS1=OMISSIONS1,  TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Omissions')
    if OMISSIONS1:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=False, TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Omissions1')
    if OMISSIONS & OMISSIONS1:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=False, OMISSIONS1=False, TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('All Omissions')
    if TIMING1:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=False,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing1')
    if TIMING2:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=False,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing2')
    if TIMING3:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=False,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing3')
    if TIMING4:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=False,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing4')
    if TIMING5:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=False,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing5')
    if TIMING6:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1,  TIMING1=TIMING1, TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=False,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing6')
    if TIMING7:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=False,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing7')
    if TIMING8:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=False,TIMING9=TIMING9,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing8')
    if TIMING9:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=False,TIMING10=TIMING10)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing9')
    if TIMING10:
        hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=False)    
        cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
        models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
        labels.append('Timing10')

    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10, TIMING1D=False)    
    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    labels.append('Timing')

    #if TIMING1 & TIMING2:
    #    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=False,  TIMING2=False,TIMING3=True,TIMING4=True,TIMING5=True,TIMING6=True,TIMING7=True,TIMING8=True,TIMING9=True,TIMING10=True)    
    #    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #    labels.append('Timing1/2')
    #if TIMING3 & TIMING4:
    #    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=True,  TIMING2=True,TIMING3=False,TIMING4=False,TIMING5=True,TIMING6=True,TIMING7=True,TIMING8=True,TIMING9=True,TIMING10=True)    
    #    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #    labels.append('Timing3/4')
    #if TIMING5 & TIMING6:
    #    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=True,  TIMING2=True,TIMING3=True,TIMING4=True,TIMING5=False,TIMING6=False,TIMING7=True,TIMING8=True,TIMING9=True,TIMING10=True)    
    #    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #    labels.append('Timing5/6')
    #if TIMING7 & TIMING8:
    #    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=True,  TIMING2=True,TIMING3=True,TIMING4=True,TIMING5=True,TIMING6=True,TIMING7=False,TIMING8=False,TIMING9=True,TIMING10=True)    
    #    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #    labels.append('Timing7/8')
    #if TIMING9 & TIMING10:
    #    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=True,  TIMING2=True,TIMING3=True,TIMING4=True,TIMING5=True,TIMING6=True,TIMING7=True,TIMING8=True,TIMING9=False,TIMING10=False)    
    #    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #    models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #    labels.append('Timing9/10')

    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=False,  TIMING2=False,TIMING3=False,TIMING4=False,TIMING5=False,TIMING6=True,TIMING7=True,TIMING8=True,TIMING9=True,TIMING10=True)    
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Timing 1-5')

    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=True,  TIMING2=True,TIMING3=True,TIMING4=True,TIMING5=True,TIMING6=False,TIMING7=False,TIMING8=False,TIMING9=False,TIMING10=False)    
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Timing 6-10')

    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=TASK0,TASK1=TASK1, TASKCR=TASKCR, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=False,  TIMING2=False,TIMING3=False,TIMING4=False,TIMING5=False,TIMING6=False,TIMING7=False,TIMING8=False,TIMING9=False,TIMING10=False)    
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('All timing')

    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=False,TASK1=True, TASKCR=False, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Full-Task1')
    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=True,TASK1=True, TASKCR=True, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Full-all Task')
    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=BIAS, TASK0=True,TASK1=False, TASKCR=True, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Task 0/CR')
    #hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,BIAS=False, TASK0=True,TASK1=False, TASKCR=True, OMISSIONS=OMISSIONS, OMISSIONS1=OMISSIONS1, TIMING1=TIMING1,  TIMING2=TIMING2,TIMING3=TIMING3,TIMING4=TIMING4,TIMING5=TIMING5,TIMING6=TIMING6,TIMING7=TIMING7,TIMING8=TIMING8,TIMING9=TIMING9,TIMING10=TIMING10)
    #cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    #models.append((hyp, evd, wMode, hess, credibleInt,weights,cross_results))
    #labels.append('Task 0/CR, no bias')

    return models,labels

def plot_dropout(models, labels,filename=None):
    '''
        Plots the dropout results for a single session
        
    '''
    plt.figure(figsize=(10,3.5))
    ax = plt.gca()
    for i in np.arange(0,len(models)):
        if np.mod(i,2) == 0:
            plt.axvspan(i-.5,i+.5,color='k', alpha=0.1)
        plt.plot(i, (1-models[i][1]/models[0][1])*100, 'ko')
    #plt.xlim(0,N)
    plt.xlabel('Model Component',fontsize=12)
    plt.ylabel('% change in evidence',fontsize=12)
    ax.tick_params(axis='both',labelsize=10)
    ax.set_xticks(np.arange(0,len(models)))
    ax.set_xticklabels(labels,rotation=90)
    plt.tight_layout()
    ax.axhline(0,color='k',alpha=0.2)
    plt.ylim(ymax=5,ymin=-20)
    if not (type(filename) == type(None)):
        plt.savefig(filename+"_dropout.png")

def plot_summaries(psydata):
    '''
    Debugging function that plots the moving average of many behavior variables 
    '''
    fig,ax = plt.subplots(nrows=8,ncols=1, figsize=(10,10),frameon=False)
    ax[0].plot(pgt.moving_mean(psydata['hits'],80),'b')
    ax[0].set_ylim(0,.15); ax[0].set_ylabel('hits')
    ax[1].plot(pgt.moving_mean(psydata['misses'],80),'r')
    ax[1].set_ylim(0,.15); ax[1].set_ylabel('misses')
    ax[2].plot(pgt.moving_mean(psydata['false_alarms'],80),'g')
    ax[2].set_ylim(0,.15); ax[2].set_ylabel('false_alarms')
    ax[3].plot(pgt.moving_mean(psydata['correct_reject'],80),'c')
    ax[3].set_ylim(0,.15); ax[3].set_ylabel('correct_reject')
    ax[4].plot(pgt.moving_mean(psydata['aborts'],80),'b')
    ax[4].set_ylim(0,.4); ax[4].set_ylabel('aborts')
    total_rate = pgt.moving_mean(psydata['hits'],80)+ pgt.moving_mean(psydata['misses'],80)+pgt.moving_mean(psydata['false_alarms'],80)+ pgt.moving_mean(psydata['correct_reject'],80)
    ax[5].plot(total_rate,'k')
    ax[5].set_ylim(0,.15); ax[5].set_ylabel('trial-rate')
    #ax[5].plot(total_rate,'b')
    ax[6].set_ylim(0,.15); ax[6].set_ylabel('d\' trials')
    ax[7].set_ylim(0,.15); ax[7].set_ylabel('d\' flashes')   
    for i in np.arange(0,len(ax)):
        ax[i].spines['top'].set_visible(False)
        ax[i].spines['right'].set_visible(False)
        ax[i].yaxis.set_ticks_position('left')
        ax[i].xaxis.set_ticks_position('bottom')
        ax[i].set_xticklabels([])

def get_timing_params(wMode):
    y = np.mean(wMode,1)[3:]
    x = np.array([1,10,2,3,4,5,6,7,8,9])
    def sigmoid(x,a,b,c,d):
        y = d+(a-d)/(1+(x/c)**b)
        return y
    x_popt,x_pcov = curve_fit(sigmoid, x,y,p0=[0,1,1,-3.5]) 
    return np.array([x_popt[1],x_popt[2]])

def process_training_session(bsid,complete=True,directory=None,format_options={}):
    '''
        Fits the model, does bootstrapping for parameter recovery, and dropout analysis and cross validation
        bsid, behavior_session_id
    
    '''
    if type(directory) == type(None):
        print('Couldnt find a directory, resulting to default')
        directory = global_directory
    
    filename = directory + str(bsid) + "_training"
    print(filename)  

    # Check if this fit has already completed
    if os.path.isfile(filename+".pkl"):
        print('Already completed this fit, quitting')
        return
    print('Starting Fit now')
    if type(bsid) == type(''):
        bsid = int(bsid)
    
    print('Doing 1D average fit')
    print("Pulling Data")
    session = pgt.get_training_data(bsid)

    print("Annotating lick bouts")
    pm.annotate_licks(session) 
    pm.annotate_bouts(session)

    print("Formating Data")
    format_options['ignore_trial_errors'] = True
    psydata = format_session(session,format_options)

    print("Initial Fit")    
    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,OMISSIONS1=False)
    ypred,ypred_each = compute_ypred(psydata, wMode,weights)
    plot_weights(wMode, weights,psydata,errorbar=credibleInt, ypred = ypred,filename=filename)
    
    print("Cross Validation Analysis")
    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    cv_pred = compute_cross_validation_ypred(psydata, cross_results,ypred)

    if complete:
        print("Dropout Analysis")
        models, labels = dropout_analysis(psydata,OMISSIONS=False, OMISSIONS1=False)
        plot_dropout(models,labels,filename=filename)

    print('Packing up and saving')
    try:
        metadata = session.metadata
    except:
        metadata = []
    if complete:
        output = [models,    labels,    hyp,   evd,   wMode,   hess,   credibleInt,   weights,   ypred,  psydata,  cross_results,  cv_pred,  metadata]
        labels = ['models', 'labels',  'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
    else:
        output = [ hyp,   evd,   wMode,   hess,   credibleInt,   weights,   ypred,  psydata,  cross_results,  cv_pred,  metadata]
        labels = ['hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']       
    fit = dict((x,y) for x,y in zip(labels, output))
    fit['ID'] = bsid

    save(filename+".pkl", fit) 

    if complete:
        fit = cluster_fit(fit,directory=directory) # gets saved separately

    save(filename+".pkl", fit) 
    plt.close('all')
 
def process_session(bsid,complete=True,directory=None,format_options={},do_timing_comparisons=False):
    '''
        Fits the model, does bootstrapping for parameter recovery, and dropout analysis and cross validation
        bsid, behavior_session_id
    
    '''
    if type(directory) == type(None):
        print('Couldnt find a directory, resulting to default')
        directory = global_directory
    
    filename = directory + str(bsid)
    print(filename)  

    # Check if this fit has already completed
    if os.path.isfile(filename+".pkl"):
        print('Already completed this fit, quitting')
        return
    print('Starting Fit now')
    if type(bsid) == type(''):
        bsid = int(bsid)
 
    if do_timing_comparisons:
        print('Doing Preliminary Fit to get Timing Regressor')
        pre_session = pgt.get_data(bsid)
        pm.annotate_licks(pre_session) 
        pm.annotate_bouts(pre_session)
        pre_psydata = format_session(pre_session,format_options)
        pre_hyp, pre_evd, pre_wMode, pre_hess, pre_credibleInt,pre_weights = fit_weights(pre_psydata,TIMING1=True,TIMING2=True,TIMING3=True,TIMING4=True,TIMING5=True,TIMING6=True,TIMING7=True,TIMING8=True,TIMING9=True,TIMING10=True,TIMING1D=False, TIMING1D_SESSION=False)
        pre_ypred,pre_ypred_each = compute_ypred(pre_psydata, pre_wMode,pre_weights)
        plot_weights(pre_wMode, pre_weights,pre_psydata,errorbar=pre_credibleInt, ypred = pre_ypred,filename=filename+"_preliminary")
        pre_cross_results = compute_cross_validation(pre_psydata, pre_hyp, pre_weights,folds=10)
        pre_cv_pred = compute_cross_validation_ypred(pre_psydata, pre_cross_results,pre_ypred)
        format_options['timing_params_session'] = get_timing_params(pre_wMode)
        preliminary = {'hyp':pre_hyp, 'evd':pre_evd, 'wMode':pre_wMode,'hess':pre_hess,'credibleInt':pre_credibleInt,'weights':pre_weights,'ypred':pre_ypred,'cross_results':pre_cross_results,'cv_pred':pre_cv_pred,'timing_params_session':format_options['timing_params_session']}

        print('Doing 1D session fit')
        s_session = pgt.get_data(bsid)
        pm.annotate_licks(s_session) 
        pm.annotate_bouts(s_session)
        s_psydata = format_session(s_session,format_options)
        s_hyp, s_evd, s_wMode, s_hess, s_credibleInt,s_weights = fit_weights(s_psydata,TIMING1D_SESSION=True, TIMING1D=False)
        s_ypred,s_ypred_each = compute_ypred(s_psydata, s_wMode,s_weights)
        plot_weights(s_wMode, s_weights,s_psydata,errorbar=s_credibleInt, ypred = s_ypred,filename=filename+"_session_timing")
        s_cross_results = compute_cross_validation(s_psydata, s_hyp, s_weights,folds=10)
        s_cv_pred = compute_cross_validation_ypred(s_psydata, s_cross_results,s_ypred)
        session_timing = {'hyp':s_hyp, 'evd':s_evd, 'wMode':s_wMode,'hess':s_hess,'credibleInt':s_credibleInt,'weights':s_weights,'ypred':s_ypred,'cross_results':s_cross_results,'cv_pred':s_cv_pred,'timing_params_session':format_options['timing_params_session']}
    
    print('Doing 1D average fit')
    print("Pulling Data")
    session = pgt.get_data(bsid)
    print("Annotating lick bouts")
    pm.annotate_licks(session) 
    pm.annotate_bouts(session)
    print("Formating Data")
    psydata = format_session(session,format_options)
    print("Initial Fit")
    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata)
    ypred,ypred_each = compute_ypred(psydata, wMode,weights)
    plot_weights(wMode, weights,psydata,errorbar=credibleInt, ypred = ypred,filename=filename)
    print("Cross Validation Analysis")
    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    cv_pred = compute_cross_validation_ypred(psydata, cross_results,ypred)

    if complete:
        print("Dropout Analysis")
        models, labels = dropout_analysis(psydata)
        plot_dropout(models,labels,filename=filename)

    print('Packing up and saving')
    try:
        metadata = session.metadata
    except:
        metadata = []
    if complete:
        output = [models,    labels,    hyp,   evd,   wMode,   hess,   credibleInt,   weights,   ypred,  psydata,  cross_results,  cv_pred,  metadata]
        labels = ['models', 'labels',  'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
    else:
        output = [ hyp,   evd,   wMode,   hess,   credibleInt,   weights,   ypred,  psydata,  cross_results,  cv_pred,  metadata]
        labels = ['hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']       
    fit = dict((x,y) for x,y in zip(labels, output))
    fit['ID'] = bsid

    if do_timing_comparisons:
        fit['preliminary'] = preliminary
        fit['session_timing'] = session_timing

    save(filename+".pkl", fit) 

    if complete:
        fit = cluster_fit(fit,directory=directory) # gets saved separately

    save(filename+".pkl", fit) 
    plt.close('all')
    
def plot_session_summary_priors(IDS,directory=None,savefig=False,group_label="",fs1=12,fs2=12,filetype='.png'):
    '''
        Make a summary plot of the priors on each feature
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(4,6))
    alld = []
    counter = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass 
        else:
            sigmas = session_summary[0]
            weights = session_summary[1]
            ax.plot(np.arange(0,len(sigmas)),sigmas, 'o',alpha = 0.5)
            plt.yscale('log')
            plt.ylim(0.0001, 20)
            ax.set_xticks(np.arange(0,len(sigmas)))
            weights_list = clean_weights(get_weights_list(weights))
            ax.set_xticklabels(weights_list,fontsize=fs2,rotation=90)
            plt.ylabel('Smoothing Prior, $\sigma$\n <-- smooth           variable --> ',fontsize=fs1)
            counter +=1
            alld.append(sigmas)            

    if counter == 0:
        print('NO DATA')
        return
    alld = np.mean(np.vstack(alld),0)
    for i in np.arange(0, len(sigmas)):
        ax.plot([i-.25, i+.25],[alld[i],alld[i]], 'k-',lw=3)
        if np.mod(i,2) == 0:
            plt.axvspan(i-.5,i+.5,color='k', alpha=0.1)
    ax.axhline(0.001,color='k',alpha=0.2)
    ax.axhline(0.01,color='k',alpha=0.2)
    ax.axhline(0.1,color='k',alpha=0.2)
    ax.axhline(1,color='k',alpha=0.2)
    ax.axhline(10,color='k',alpha=0.2)
    plt.yticks(fontsize=fs2-4,rotation=90)
    ax.xaxis.tick_top()
    ax.set_xlim(xmin=-.5)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"prior"+filetype)


def plot_session_summary_correlation(IDS,directory=None,savefig=False,group_label="",verbose=True):
    '''
        Make a summary plot of the priors on each feature
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(5,4))
    scores = []
    ids = []
    counter = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            fit = session_summary[7]
            r2 = compute_model_prediction_correlation(fit,fit_mov=25,data_mov=25,plot_this=False,cross_validation=True)
            scores.append(r2)
            ids.append(id)
            counter +=1

    if counter == 0:
        print('NO DATA')
        return

    ax.hist(np.array(scores),bins=50)
    ax.set_ylabel('Count', fontsize=12)
    ax.set_xlabel('$R^2$', fontsize=12)
    ax.xaxis.set_tick_params(labelsize=12)
    ax.yaxis.set_tick_params(labelsize=12)
    meanscore = np.median(np.array(scores))
    ax.plot(meanscore, ax.get_ylim()[1],'rv')
    ax.axvline(meanscore,color='r', alpha=0.3)
    ax.set_xlim(0,1)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"correlation.png")
    if verbose:
        median = np.argsort(np.array(scores))[len(scores)//2]
        best = np.argmax(np.array(scores))
        worst = np.argmin(np.array(scores)) 
        print('R^2 Correlation:')
        print('Worst  Session: ' + str(ids[worst]) + " " + str(scores[worst]))
        print('Median Session: ' + str(ids[median]) + " " + str(scores[median]))
        print('Best   Session: ' + str(ids[best]) + " " + str(scores[best]))      
    return scores, ids 

def plot_session_summary_dropout(IDS,directory=None,cross_validation=True,savefig=False,group_label="",model_evidence=False,fs1=12,fs2=12,filetype='.png'):
    '''
        Make a summary plot showing the fractional change in either model evidence (not cross-validated), or log-likelihood (cross-validated)
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(7.2,6))
    alld = []
    counter = 0
    ax.axhline(0,color='k',alpha=0.2)
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory, cross_validation_dropout=cross_validation,model_evidence=model_evidence)
        except:
            pass
        else:
            dropout = session_summary[2]
            labels  = session_summary[3]
            ax.plot(np.arange(0,len(dropout)),dropout, 'o',alpha=0.5)
            ax.set_xticks(np.arange(0,len(dropout)))
            ax.set_xticklabels(clean_dropout(labels),fontsize=fs2, rotation = 90)
            if model_evidence:
                plt.ylabel('% Change in Model Evidence \n <-- Worse Fit',fontsize=fs1)
            else:
                if cross_validation:
                    plt.ylabel('% Change in CV Likelihood \n <-- Worse Fit',fontsize=fs1)
                else:
                    plt.ylabel('% Change in Likelihood \n <-- Worse Fit',fontsize=fs1)
            alld.append(dropout)
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    alld = np.mean(np.vstack(alld),0)
    plt.yticks(fontsize=fs2-4,rotation=90)
    for i in np.arange(0, len(dropout)):
        ax.plot([i-.25, i+.25],[alld[i],alld[i]], 'k-',lw=3)
        if np.mod(i,2) == 0:
            plt.axvspan(i-.5,i+.5,color='k', alpha=0.1)
    ax.xaxis.tick_top()
    plt.tight_layout()
    plt.xlim(-0.5,len(dropout) - 0.5)
    plt.ylim(-80,5)
    if savefig:
        if model_evidence:
            plt.savefig(directory+"summary_"+group_label+"dropout_model_evidence"+filetype)
        elif cross_validation:
            plt.savefig(directory+"summary_"+group_label+"dropout_cv"+filetype)
        else:
            plt.savefig(directory+"summary_"+group_label+"dropout"+filetype)

def plot_session_summary_weights(IDS,directory=None, savefig=False,group_label="",return_weights=False,fs1=12,fs2=12,filetype='.svg'):
    '''
        Makes a summary plot showing the average weight value for each session
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(4,6))
    counter = 0
    ax.axhline(0,color='k',alpha=0.2)
    all_weights = []
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            avgW = session_summary[4]
            weights  = session_summary[1]
            ax.plot(np.arange(0,len(avgW)),avgW, 'o',alpha=0.5)
            ax.set_xticks(np.arange(0,len(avgW)))
            plt.ylabel('Avg. Weights across each session',fontsize=fs1)

            all_weights.append(avgW)
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    allW = np.mean(np.vstack(all_weights),0)
    for i in np.arange(0, len(avgW)):
        ax.plot([i-.25, i+.25],[allW[i],allW[i]], 'k-',lw=3)
        if np.mod(i,2) == 0:
            plt.axvspan(i-.5,i+.5,color='k', alpha=0.1)
    weights_list = get_weights_list(weights)
    ax.set_xticklabels(clean_weights(weights_list),fontsize=fs2, rotation = 90)
    ax.xaxis.tick_top()
    plt.yticks(fontsize=fs2-4,rotation=90)
    plt.tight_layout()
    plt.xlim(-0.5,len(avgW) - 0.5)
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weights"+filetype)
    if return_weights:
        return all_weights

def plot_session_summary_weight_range(IDS,directory=None,savefig=False,group_label=""):
    '''
        Makes a summary plot showing the range of each weight across each session
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(4,6))
    allW = None
    counter = 0
    ax.axhline(0,color='k',alpha=0.2)
    all_range = []
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            rangeW = session_summary[5]
            weights  = session_summary[1]
            ax.plot(np.arange(0,len(rangeW)),rangeW, 'o',alpha=0.5)
            ax.set_xticks(np.arange(0,len(rangeW)))
            plt.ylabel('Range of Weights across each session',fontsize=12)
            all_range.append(rangeW)    
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    allW = np.mean(np.vstack(all_range),0)
    for i in np.arange(0, len(rangeW)):
        ax.plot([i-.25, i+.25],[allW[i],allW[i]], 'k-',lw=3)
        if np.mod(i,2) == 0:
            plt.axvspan(i-.5,i+.5,color='k', alpha=0.1)
    weights_list = get_weights_list(weights)
    ax.set_xticklabels(clean_weights(weights_list),fontsize=12, rotation = 90)
    ax.xaxis.tick_top()
    plt.yticks(fontsize=12)
    plt.tight_layout()
    plt.xlim(-0.5,len(rangeW) - 0.5)
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_range.png")

def plot_session_summary_weight_scatter(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a scatter plot of each weight against each other weight, plotting the average weight for each session
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=nel,ncols=nel,figsize=(11,10))
    allW = None
    counter = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory= directory)
        except:
            pass
        else:
            W = session_summary[6]
            weights  = session_summary[1]
            weights_list = get_weights_list(weights)
            for i in np.arange(0,np.shape(W)[0]):
                if i < np.shape(W)[0]-1:
                    for j in np.arange(1, i+1):
                        ax[i,j-1].tick_params(top='off',bottom='off', left='off',right='off')
                        ax[i,j-1].set_xticks([])
                        ax[i,j-1].set_yticks([])
                        for spine in ax[i,j-1].spines.values():
                            spine.set_visible(False)
                for j in np.arange(i+1,np.shape(W)[0]):
                    ax[i,j-1].axvline(0,color='k',alpha=0.05)
                    ax[i,j-1].axhline(0,color='k',alpha=0.05)
                    ax[i,j-1].plot(W[j,:], W[i,:],'o', alpha=0.01)
                    ax[i,j-1].set_xlabel(weights_list[j],fontsize=12)
                    ax[i,j-1].set_ylabel(weights_list[i],fontsize=12)
                    ax[i,j-1].xaxis.set_tick_params(labelsize=12)
                    ax[i,j-1].yaxis.set_tick_params(labelsize=12)
            counter +=1
    plt.tight_layout()
    if counter == 0:
        print('NO DATA')
        return
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_scatter.png")

def plot_session_summary_dropout_scatter(IDS,directory=None,savefig=False,group_label=""):
    '''
        Makes a scatter plot of the dropout performance change for each feature against each other feature 
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    

    allW = None
    counter = 0
    first = True
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory, cross_validation_dropout=True)
        except:
            pass
        else:
            if first:
                fig,ax = plt.subplots(nrows=len(session_summary[2])-2,ncols=len(session_summary[2])-2,figsize=(11,10))        
                first = False 
            d = session_summary[2][1:]
            l = session_summary[3][1:]
            dropout = d
            labels = l
            for i in np.arange(0,np.shape(dropout)[0]):
                if i < np.shape(dropout)[0]-1:
                    for j in np.arange(1, i+1):
                        ax[i,j-1].tick_params(top='off',bottom='off', left='off',right='off')
                        ax[i,j-1].set_xticks([])
                        ax[i,j-1].set_yticks([])
                        for spine in ax[i,j-1].spines.values():
                            spine.set_visible(False)
                for j in np.arange(i+1,np.shape(dropout)[0]):
                    ax[i,j-1].axvline(0,color='k',alpha=0.1)
                    ax[i,j-1].axhline(0,color='k',alpha=0.1)
                    ax[i,j-1].plot(dropout[j], dropout[i],'o',alpha=0.5)
                    ax[i,j-1].set_xlabel(clean_dropout([labels[j]])[0],fontsize=12)
                    ax[i,j-1].set_ylabel(clean_dropout([labels[i]])[0],fontsize=12)
                    ax[i,j-1].xaxis.set_tick_params(labelsize=12)
                    ax[i,j-1].yaxis.set_tick_params(labelsize=12)
                    if i == 0:
                        ax[i,j-1].set_ylim(-80,5)
            counter+=1
    if counter == 0:
        print('NO DATA')
        return
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"dropout_scatter.png")


def plot_session_summary_weight_avg_scatter(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a scatter plot of each weight against each other weight, plotting the average weight for each session
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=nel,ncols=nel,figsize=(11,10))
    allW = None
    counter = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            weights  = session_summary[1]
            weights_list = get_weights_list(weights)
            for i in np.arange(0,np.shape(W)[0]):
                if i < np.shape(W)[0]-1:
                    for j in np.arange(1, i+1):
                        ax[i,j-1].tick_params(top='off',bottom='off', left='off',right='off')
                        ax[i,j-1].set_xticks([])
                        ax[i,j-1].set_yticks([])
                        for spine in ax[i,j-1].spines.values():
                            spine.set_visible(False)
                for j in np.arange(i+1,np.shape(W)[0]):
                    ax[i,j-1].axvline(0,color='k',alpha=0.1)
                    ax[i,j-1].axhline(0,color='k',alpha=0.1)
                    meanWj = np.mean(W[j,:])
                    meanWi = np.mean(W[i,:])
                    stdWj = np.std(W[j,:])
                    stdWi = np.std(W[i,:])
                    ax[i,j-1].plot([meanWj, meanWj], meanWi+[-stdWi, stdWi],'k-',alpha=0.1)
                    ax[i,j-1].plot(meanWj+[-stdWj,stdWj], [meanWi, meanWi],'k-',alpha=0.1)
                    ax[i,j-1].plot(meanWj, meanWi,'o',alpha=0.5)
                    ax[i,j-1].set_xlabel(clean_weights([weights_list[j]])[0],fontsize=12)
                    ax[i,j-1].set_ylabel(clean_weights([weights_list[i]])[0],fontsize=12)
                    ax[i,j-1].xaxis.set_tick_params(labelsize=12)
                    ax[i,j-1].yaxis.set_tick_params(labelsize=12)
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_avg_scatter.png")

def plot_session_summary_weight_avg_scatter_task0(IDS,directory=None,savefig=False,group_label="",nel=3,fs1=12,fs2=12,filetype='.png',plot_error=True):
    '''
        Makes a summary plot of the average weights of task0 against omission weights for each session
        Also computes a regression line, and returns the linear model
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=1,ncols=1,figsize=(3,4))
    allx = []
    ally = []
    counter = 0
    ax.axvline(0,color='k',alpha=0.5,ls='--')
    ax.axhline(0,color='k',alpha=0.5,ls='--')
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            weights  = session_summary[1]
            weights_list = get_weights_list(weights)
            xdex = np.where(np.array(weights_list) == 'task0')[0][0]
            ydex = np.where(np.array(weights_list) == 'omissions1')[0][0]

            meanWj = np.mean(W[xdex,:])
            meanWi = np.mean(W[ydex,:])
            allx.append(meanWj)
            ally.append(meanWi)
            stdWj = np.std(W[xdex,:])
            stdWi = np.std(W[ydex,:])
            if plot_error:
                ax.plot([meanWj, meanWj], meanWi+[-stdWi, stdWi],'k-',alpha=0.1)
                ax.plot(meanWj+[-stdWj,stdWj], [meanWi, meanWi],'k-',alpha=0.1)
            ax.plot(meanWj, meanWi,'ko',alpha=0.5)
            ax.set_xlabel(clean_weights([weights_list[xdex]])[0],fontsize=fs1)
            ax.set_ylabel(clean_weights([weights_list[ydex]])[0],fontsize=fs1)
            ax.xaxis.set_tick_params(labelsize=fs2)
            ax.yaxis.set_tick_params(labelsize=fs2)
            counter+=1
    if counter == 0:
        print('NO DATA')
        return
    x = np.array(allx).reshape((-1,1))
    y = np.array(ally)
    model = LinearRegression(fit_intercept=False).fit(x,y)
    sortx = np.sort(allx).reshape((-1,1))
    y_pred = model.predict(sortx)
    ax.plot(sortx,y_pred, 'r--')
    score = round(model.score(x,y),2)
    #plt.text(sortx[0],y_pred[-1],"Omissions = "+str(round(model.coef_[0],2))+"*Task \nr^2 = "+str(score),color="r",fontsize=fs2)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_avg_scatter_task0"+filetype)
    return model


def plot_session_summary_weight_avg_scatter_hits(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a scatter plot of each weight against the total number of hits
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=2,ncols=nel+1,figsize=(14,6))
    allW = None
    counter = 0
    xmax = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            fit = session_summary[7]
            hits = np.sum(fit['psydata']['hits'])
            xmax = np.max([hits, xmax])
            weights  = session_summary[1]
            weights_list = get_weights_list(weights)
            for i in np.arange(0,np.shape(W)[0]):
                ax[0,i].axhline(0,color='k',alpha=0.1)
                meanWi = np.mean(W[i,:])
                stdWi = np.std(W[i,:])
                ax[0,i].plot([hits, hits], meanWi+[-stdWi, stdWi],'k-',alpha=0.1)
                ax[0,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[0,i].set_xlabel('hits',fontsize=12)
                ax[0,i].set_ylabel(clean_weights([weights_list[i]])[0],fontsize=12)
                ax[0,i].xaxis.set_tick_params(labelsize=12)
                ax[0,i].yaxis.set_tick_params(labelsize=12)
                ax[0,i].set_xlim(xmin=0,xmax=xmax)

                meanWi = transform(np.mean(W[i,:]))
                stdWiPlus = transform(np.mean(W[i,:])+np.std(W[i,:]))
                stdWiMinus =transform(np.mean(W[i,:])-np.std(W[i,:])) 
                ax[1,i].plot([hits, hits], [stdWiMinus, stdWiPlus],'k-',alpha=0.1)
                ax[1,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[1,i].set_xlabel('hits',fontsize=12)
                ax[1,i].set_ylabel(clean_weights([weights_list[i]])[0],fontsize=12)
                ax[1,i].xaxis.set_tick_params(labelsize=12)
                ax[1,i].yaxis.set_tick_params(labelsize=12)
                ax[1,i].set_xlim(xmin=0,xmax=xmax)
                ax[1,i].set_ylim(ymin=0,ymax=1)

            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_avg_scatter_hits.png")

def plot_session_summary_weight_avg_scatter_false_alarms(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a scatter plot of each weight against the total number of false_alarms
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=2,ncols=nel+1,figsize=(14,6))
    allW = None
    counter = 0
    xmax = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            fit = session_summary[7]
            hits = np.sum(fit['psydata']['false_alarms'])
            xmax = np.max([hits, xmax])
            weights  = session_summary[1]
            weights_list = clean_weights(get_weights_list(weights))
            for i in np.arange(0,np.shape(W)[0]):
                ax[0,i].axhline(0,color='k',alpha=0.1)
                meanWi = np.mean(W[i,:])
                stdWi = np.std(W[i,:])
                ax[0,i].plot([hits, hits], meanWi+[-stdWi, stdWi],'k-',alpha=0.1)
                ax[0,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[0,i].set_xlabel('false_alarms',fontsize=12)
                ax[0,i].set_ylabel(weights_list[i],fontsize=12)
                ax[0,i].xaxis.set_tick_params(labelsize=12)
                ax[0,i].yaxis.set_tick_params(labelsize=12)
                ax[0,i].set_xlim(xmin=0,xmax=xmax)

                meanWi = transform(np.mean(W[i,:]))
                stdWiPlus = transform(np.mean(W[i,:])+np.std(W[i,:]))
                stdWiMinus =transform(np.mean(W[i,:])-np.std(W[i,:])) 
                ax[1,i].plot([hits, hits], [stdWiMinus, stdWiPlus],'k-',alpha=0.1)
                ax[1,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[1,i].set_xlabel('false_alarms',fontsize=12)
                ax[1,i].set_ylabel(weights_list[i],fontsize=12)
                ax[1,i].xaxis.set_tick_params(labelsize=12)
                ax[1,i].yaxis.set_tick_params(labelsize=12)
                ax[1,i].set_xlim(xmin=0,xmax=xmax)
                ax[1,i].set_ylim(ymin=0,ymax=1)

            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_avg_scatter_false_alarms.png")

def plot_session_summary_weight_avg_scatter_miss(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a scatter plot of each weight against the total number of miss
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=2,ncols=nel+1,figsize=(14,6))
    allW = None
    counter = 0
    xmax = 0
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            fit = session_summary[7]
            hits = np.sum(fit['psydata']['misses'])
            xmax = np.max([hits, xmax])
            weights  = session_summary[1]
            weights_list = clean_weights(get_weights_list(weights))
            for i in np.arange(0,np.shape(W)[0]):
                ax[0,i].axhline(0,color='k',alpha=0.1)
                meanWi = np.mean(W[i,:])
                stdWi = np.std(W[i,:])
                ax[0,i].plot([hits, hits], meanWi+[-stdWi, stdWi],'k-',alpha=0.1)
                ax[0,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[0,i].set_xlabel('misses',fontsize=12)
                ax[0,i].set_ylabel(weights_list[i],fontsize=12)
                ax[0,i].xaxis.set_tick_params(labelsize=12)
                ax[0,i].yaxis.set_tick_params(labelsize=12)
                ax[0,i].set_xlim(xmin=0,xmax=xmax)

                meanWi = transform(np.mean(W[i,:]))
                stdWiPlus = transform(np.mean(W[i,:])+np.std(W[i,:]))
                stdWiMinus =transform(np.mean(W[i,:])-np.std(W[i,:])) 
                ax[1,i].plot([hits, hits], [stdWiMinus, stdWiPlus],'k-',alpha=0.1)
                ax[1,i].plot(hits, meanWi,'o',alpha=0.5)
                ax[1,i].set_xlabel('misses',fontsize=12)
                ax[1,i].set_ylabel(weights_list[i],fontsize=12)
                ax[1,i].xaxis.set_tick_params(labelsize=12)
                ax[1,i].yaxis.set_tick_params(labelsize=12)
                ax[1,i].set_xlim(xmin=0,xmax=xmax)
                ax[1,i].set_ylim(ymin=0,ymax=1)

            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_avg_scatter_misses.png")

def plot_session_summary_weight_trajectory(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a summary plot by plotting each weights trajectory across each session. Plots the average trajectory in bold
        this function is super hacky. average is wrong, and doesnt properly align time due to consumption bouts. But gets the general pictures. 
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=nel+1,ncols=1,figsize=(6,10))
    allW = []
    counter = 0
    xmax  =  []
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            W = session_summary[6]
            weights  = session_summary[1]
            weights_list = clean_weights(get_weights_list(weights))
            for i in np.arange(0,np.shape(W)[0]):
                ax[i].plot(W[i,:],alpha = 0.2)
                ax[i].set_ylabel(weights_list[i],fontsize=12)

                xmax.append(len(W[i,:]))
                ax[i].set_xlim(0,np.max(xmax))
                ax[i].xaxis.set_tick_params(labelsize=12)
                ax[i].yaxis.set_tick_params(labelsize=12)
                if i == np.shape(W)[0] -1:
                    ax[i].set_xlabel('Flash #',fontsize=12)
            W = np.pad(W,([0,0],[0,4000]),'constant',constant_values=0)
            allW.append(W[:,0:4000])
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    allW = np.mean(np.array(allW),0)
    for i in np.arange(0,np.shape(W)[0]):
        ax[i].axhline(0, color='k')
        ax[i].plot(allW[i,:],'k',alpha = 1,lw=3)
        if i> 0:
            ax[i].set_ylim(ymin=-2.5)
        ax[i].set_xlim(0,4000)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_trajectory.png")

def get_cross_validation_dropout(cv_results):
    '''
        computes the full log likelihood by summing each cross validation fold
    '''
    return np.sum([i['logli'] for i in cv_results]) 

def get_Excit_IDS(all_metadata):
    '''
        Given a list of metadata (get_all_metadata), returns a list of IDS with excitatory CRE lines
    '''
    raise Exception('outdated')
    IDS =[]
    for m in all_metadata:
        if m['full_genotype'][0:5] == 'Slc17':
            IDS.append(m['ophys_experiment_id'])
    return IDS

def get_Inhib_IDS(all_metadata):
    '''
        Given a list of metadata (get_all_metadata), returns a list of IDS with inhibitory CRE lines
    '''
    raise Exception('outdated')
    IDS =[]
    for m in all_metadata:
        if not( m['full_genotype'][0:5] == 'Slc17'):
            IDS.append(m['ophys_experiment_id'])
    return IDS

def get_stage_names(IDS):
    '''
        Compiles a list of the stage number for each ophys session
    '''
    stages = [[],[],[],[],[],[],[]]

    for id in IDS:
        print(id)
        try:    
            stage= pgt.get_stage(id)
        except:
            pass
        else:
            stages[int(stage[6])].append(id)
    return stages


def get_all_metadata(IDS,directory=None):
    '''
        Compiles a list of metadata for every session in IDS
    '''
    if type(directory) == type(None):
        directory = global_directory
    m = []
    for id in IDS:
        try:
            filename = directory + str(id) + ".pkl" 
            fit = load(filename)
            if not (type(fit) == type(dict())):
                labels = ['models', 'labels', 'boots', 'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
                fit = dict((x,y) for x,y in zip(labels, fit))
            metadata = fit['metadata']
            m.append(metadata)
        except:
            pass
    
    return m
           
def get_session_summary(behavior_session_id,cross_validation_dropout=True,model_evidence=False,directory=None,hit_threshold=50):
    '''
        Extracts useful summary information about each fit
        if cross_validation_dropout, then uses the dropout analysis where each reduced model is cross-validated
    '''
    if type(directory) == type(None):
        directory = global_directory

    filename = directory + str(behavior_session_id) + ".pkl" 
    fit = load(filename)
    if not (type(fit) == type(dict())) :
        labels = ['models', 'labels', 'boots', 'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
        fit = dict((x,y) for x,y in zip(labels, fit))
    if np.sum(fit['psydata']['hits']) < hit_threshold:
        raise Exception('Below hit threshold')    

    # compute statistics
    dropout = []
    if model_evidence:
        for i in np.arange(0, len(fit['models'])):
            dropout.append(fit['models'][i][1] )
        dropout = np.array(dropout)
        dropout = (1-dropout/dropout[0])*100
    elif cross_validation_dropout:
        for i in np.arange(0, len(fit['models'])):
            dropout.append(get_cross_validation_dropout(fit['models'][i][6]))
        dropout = np.array(dropout)
        dropout = (1-dropout/dropout[0])*100
    else:
        for i in np.arange(0, len(fit['models'])):
            dropout.append((1-fit['models'][i][1]/fit['models'][0][1])*100)
        dropout = np.array(dropout)
    avgW = np.mean(fit['wMode'],1)
    rangeW = np.ptp(fit['wMode'],1)
    return fit['hyp']['sigma'],fit['weights'],dropout,fit['labels'], avgW, rangeW,fit['wMode'],fit

def plot_session_summary(IDS,directory=None,savefig=False,group_label="",nel=3):
    '''
        Makes a series of summary plots for all the IDS
    '''
    if type(directory) == type(None):
        directory = global_directory
    plot_session_summary_priors(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_dropout(IDS,directory=directory,cross_validation=False,savefig=savefig,group_label=group_label)
    plot_session_summary_dropout(IDS,directory=directory,cross_validation=True,savefig=savefig,group_label=group_label)
    plot_session_summary_dropout(IDS,directory=directory,model_evidence=True,savefig=savefig,group_label=group_label)
    plot_session_summary_dropout_scatter(IDS, directory=directory, savefig=savefig, group_label=group_label) 
    plot_session_summary_weights(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_weight_range(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_weight_scatter(IDS,directory=directory,savefig=savefig,group_label=group_label,nel=nel)
    plot_session_summary_weight_avg_scatter(IDS,directory=directory,savefig=savefig,group_label=group_label,nel=nel)
    plot_session_summary_weight_avg_scatter_task0(IDS,directory=directory,savefig=savefig,group_label=group_label,nel=nel)
    plot_session_summary_weight_avg_scatter_hits(IDS,directory=directory,savefig=savefig,group_label=group_label,nel=nel)
    plot_session_summary_weight_avg_scatter_miss(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_weight_avg_scatter_false_alarms(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_weight_trajectory(IDS,directory=directory,savefig=savefig,group_label=group_label,nel=nel)
    plot_session_summary_logodds(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_correlation(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_session_summary_roc(IDS,directory=directory,savefig=savefig,group_label=group_label)
    plot_static_comparison(IDS,directory=directory,savefig=savefig,group_label=group_label)

def compute_cross_validation(psydata, hyp, weights,folds=10):
    '''
        Computes Cross Validation for the data given the regressors as defined in hyp and weights
    '''
    trainDs, testDs = Kfold_crossVal(psydata,F=folds)
    test_results = []
    for k in range(folds):
        print("running fold", k)
        _,_,wMode_K,_ = hyperOpt(trainDs[k], hyp, weights, ['sigma'])
        logli, gw = Kfold_crossVal_check(testDs[k], wMode_K, trainDs[k]['missing_trials'], weights)
        res = {'logli' : np.sum(logli), 'gw' : gw, 'test_inds' : testDs[k]['test_inds']}
        test_results += [res]
    
    check_coverage = [len(i['gw']) for i in test_results]
    if np.sum(check_coverage) != len(psydata['y']):
        print('Hit coverage error, lets see if it crashes')
        #test_results = compute_cross_validation(psydata,hyp,weights,folds=folds)
        #print('Looks like the issue is resolved, continuing...')
    return test_results

def compute_cross_validation_ypred(psydata,test_results,ypred):
    '''
        Computes the predicted outputs from cross validation results by stitching together the predictions from each folds test set
        full_pred is a vector of probabilities (0,1) for each time bin in psydata
    '''
    # combine each folds predictions
    myrange = np.arange(0, len(psydata['y']))
    xval_mask = np.ones(len(myrange)).astype(bool)
    X = np.array([i['gw'] for i in test_results]).flatten()
    test_inds = np.array([i['test_inds'] for i in test_results]).flatten()
    inrange = np.where((test_inds >= 0) & (test_inds < len(psydata['y'])))[0]
    inds = [i for i in np.argsort(test_inds) if i in inrange]
    X = X[inds]
    # because length of trial might not be perfectly divisible, there are untested indicies
    untested_inds = [j for j in myrange if j not in test_inds]
    untested_inds = [np.where(myrange == i)[0][0] for i in untested_inds]
    xval_mask[untested_inds] = False
    cv_pred = 1/(1+np.exp(-X))
    # Fill in untested indicies with ypred
    full_pred = copy.copy(ypred)
    full_pred[np.where(xval_mask==True)[0]] = cv_pred
    return  full_pred


def plot_session_summary_logodds(IDS,directory=None,savefig=False,group_label="",cross_validation=True,hit_threshold=50):
    '''
        Makes a summary plot of the log-odds of the model fits = log(prob(lick|lick happened)/prob(lick|no lick happened))
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(nrows=1,ncols=2,figsize=(10,4.5))
    logodds=[]
    counter =0
    ids= []
    for id in IDS:
        try:
            #session_summary = get_session_summary(id)
            filenamed = directory + str(id) + ".pkl" 
            output = load(filenamed)
            if not (type(output) == type(dict())):
                labels = ['models', 'labels', 'boots', 'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
                fit = dict((x,y) for x,y in zip(labels, output))
            else:
                fit = output
            if np.sum(fit['psydata']['hits']) < hit_threshold:
                raise Exception('below hit threshold')
        except:
            pass
        else:
            if cross_validation:
                lickedp = np.mean(fit['cv_pred'][fit['psydata']['y'] ==2])
                nolickp = np.mean(fit['cv_pred'][fit['psydata']['y'] ==1])
            else:
                lickedp = np.mean(fit['ypred'][fit['psydata']['y'] ==2])
                nolickp = np.mean(fit['ypred'][fit['psydata']['y'] ==1])
            ax[0].plot(nolickp,lickedp, 'o', alpha = 0.5)
            logodds.append(np.log(lickedp/nolickp))
            ids.append(id)
            counter +=1
    if counter == 0:
        print('NO DATA')
        return
    ax[0].set_ylabel('P(lick|lick)', fontsize=12)
    ax[0].set_xlabel('P(lick|no-lick)', fontsize=12)
    ax[0].plot([0,1],[0,1], 'k--',alpha=0.2)
    ax[0].xaxis.set_tick_params(labelsize=12)
    ax[0].yaxis.set_tick_params(labelsize=12)
    ax[0].set_ylim(0,1)
    ax[0].set_xlim(0,1)
    ax[1].hist(np.array(logodds),bins=30)
    ax[1].set_ylabel('Count', fontsize=12)
    ax[1].set_xlabel('Log-Odds', fontsize=12)
    ax[1].xaxis.set_tick_params(labelsize=12)
    ax[1].yaxis.set_tick_params(labelsize=12)
    meanscore = np.median(np.array(logodds))
    ax[1].plot(meanscore, ax[1].get_ylim()[1],'rv')
    ax[1].axvline(meanscore,color='r', alpha=0.3)


    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"weight_logodds.png")

    median = np.argsort(np.array(logodds))[len(logodds)//2]
    best = np.argmax(np.array(logodds))
    worst = np.argmin(np.array(logodds)) 
    print("Log-Odds Summary:")
    print('Worst  Session: ' + str(ids[worst]) + " " + str(logodds[worst]))
    print('Median Session: ' + str(ids[median]) + " " + str(logodds[median]))
    print('Best   Session: ' + str(ids[best]) + " " + str(logodds[best]))      


def get_all_weights(IDS,directory=None):
    '''
        Returns a concatenation of all weights for every session in IDS
    '''
    if type(directory) == type(None):
        directory = global_directory
    weights = None
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            if weights is None:
                weights = session_summary[6]
            else:
                weights = np.concatenate([weights, session_summary[6]],1)
    return weights

def load_fit(ID, directory=None,TRAIN=False):
    '''
        Loads the fit for session ID, in directory
        Creates a dictionary for the session
        if the fit has cluster labels then it loads them and puts them into the dictionary
    '''
    if type(directory) == type(None):
        directory = global_directory
    if TRAIN:
        filename = directory + str(ID) + "_training.pkl" 
    else:
        filename = directory + str(ID) + ".pkl" 
    output = load(filename)
    if not (type(output) == type(dict())):
        labels = ['models', 'labels', 'boots', 'hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','cross_results','cv_pred','metadata']
        fit = dict((x,y) for x,y in zip(labels, output))
    else:
        fit = output
    fit['ID'] = ID
    #if os.path.isfile(directory+str(ID) + "_clusters.pkl"):
    #    clusters = load(directory+str(ID) + "_clusters.pkl")
    #    fit['clusters'] = clusters
    #else:
    #    fit = cluster_fit(fit,directory=directory)
    if os.path.isfile(directory+str(ID) + "_all_clusters.pkl"):
        fit['all_clusters'] = load(directory+str(ID) + "_all_clusters.pkl")
    return fit

def plot_cluster(ID, cluster, fit=None, directory=None):
    if type(directory) == type(None):
        directory = global_directory
    if not (type(fit) == type(dict())):
        fit = load_fit(ID, directory=directory)
    plot_fit(ID,fit=fit, cluster_labels=fit['clusters'][str(cluster)][1])

def summarize_fit(fit, directory=None, savefig=False):
    fig,ax = plt.subplots(nrows=2,ncols=2, figsize=(10,7))
    means = np.mean(fit['wMode'],1)
    stds = np.std(fit['wMode'],1)
    my_colors = sns.color_palette("hls",len(fit['weights'].keys()))
    weights_list = clean_weights(get_weights_list(fit['weights']))
    for i in np.arange(0,len(means)):
        if np.mod(i,2) == 0:
            ax[0,0].axvspan(i-.5,i+.5,color='k', alpha=0.1)
    for i in range(0,len(means)):
        ax[0,0].plot(i,means[i],'o',color=my_colors[i],label=weights_list[i])
        ax[0,0].plot([i,i],[means[i]-stds[i],means[i]+stds[i]],'-',color=my_colors[i])
    ax[0,0].set_ylabel('Average Weight')
    ax[0,0].set_xlabel('Strategy')
    ax[0,0].axhline(0,linestyle='--',color='k',alpha=0.5)
    ax[0,0].set_xlim(-0.5,len(means)-0.5)
    ax[0,0].set_xticks(np.arange(0,len(means)))

    for i in np.arange(0,len(means)):
        if np.mod(i,2) == 0:
            ax[0,1].axvspan(i-.5,i+.5,color='k', alpha=0.1)
    ax[0,1].axhline(0,linestyle='-',color='k',    alpha=0.3)
    ax[0,1].axhline(0.1,linestyle='-',color='k',  alpha=0.3)
    ax[0,1].axhline(0.01,linestyle='-',color='k', alpha=0.3)
    ax[0,1].axhline(0.001,linestyle='-',color='k',alpha=0.3)
    for i in range(0,len(means)):
        ax[0,1].plot(i,fit['hyp']['sigma'][i],'o',color=my_colors[i],label=weights_list[i])
    ax[0,1].set_ylabel('Smoothing Prior, $\sigma$ \n <-- More Smooth      More Variable -->')
    ax[0,1].set_yscale('log')
    ax[0,1].set_xlabel('Strategy')
    ax[0,1].legend(loc='center left', bbox_to_anchor=(1, 0.5))
    ax[0,1].set_xlim(-0.5,len(means)-0.5)
    ax[0,1].set_xticks(np.arange(0,len(means)))

    dropout = get_session_dropout(fit)[1:]
    for i in np.arange(0,len(dropout)):
        if np.mod(i,2) == 0:
            ax[1,0].axvspan(i-.5,i+.5,color='k', alpha=0.1)
    ax[1,0].axhline(0,linestyle='--',color='k',    alpha=0.3)
    ax[1,0].plot(dropout,'ko')
    ax[1,0].set_ylabel('Dropout')

    ax[1,0].set_xlabel('Model Component')
    ax[1,0].tick_params(axis='both',labelsize=10)
    ax[1,0].set_xticks(np.arange(0,len(dropout)))
    labels = fit['labels'][1:]
    if type(labels) is not type(None):    
        ax[1,0].set_xticklabels(labels,rotation=90)

    for spine in ax[1,1].spines.values():
        spine.set_visible(False)
    ax[1,1].set_yticks([])
    ax[1,1].set_xticks([])
    roc_cv    = compute_model_roc(fit,cross_validation=True)
    roc_train = compute_model_roc(fit,cross_validation=False)
    fs= 12
    starty = 0.5
    offset = 0.04
    fig.text(.7,starty-offset*0,"Session:  "   ,fontsize=fs,horizontalalignment='right');           fig.text(.7,starty-offset*0,str(fit['ID']),fontsize=fs)
    if 'mouse_id' in fit['metadata']:
        fig.text(.7,starty-offset*1,"Mouse ID:  " ,fontsize=fs,horizontalalignment='right');            fig.text(.7,starty-offset*1,str(fit['metadata']['mouse_id']),fontsize=fs)
    else:
         fig.text(.7,starty-offset*1,"Mouse ID:  " ,fontsize=fs,horizontalalignment='right')   
    fig.text(.7,starty-offset*2,"Driver Line:  " ,fontsize=fs,horizontalalignment='right');         fig.text(.7,starty-offset*2,fit['metadata']['driver_line'][-1],fontsize=fs)
    fig.text(.7,starty-offset*3,"Stage:  "     ,fontsize=fs,horizontalalignment='right');           fig.text(.7,starty-offset*3,str(fit['metadata']['session_type']),fontsize=fs)
    fig.text(.7,starty-offset*4,"ROC Train:  ",fontsize=fs,horizontalalignment='right');            fig.text(.7,starty-offset*4,str(round(roc_train,2)),fontsize=fs)
    fig.text(.7,starty-offset*5,"ROC CV:  "    ,fontsize=fs,horizontalalignment='right');           fig.text(.7,starty-offset*5,str(round(roc_cv,2)),fontsize=fs)
    fig.text(.7,starty-offset*6,"Lick Fraction:  ",fontsize=fs,horizontalalignment='right');        fig.text(.7,starty-offset*6,str(round(get_lick_fraction(fit),2)),fontsize=fs)
    fig.text(.7,starty-offset*7,"Lick Hit Fraction:  ",fontsize=fs,horizontalalignment='right');    fig.text(.7,starty-offset*7,str(round(get_hit_fraction(fit),2)),fontsize=fs)
    fig.text(.7,starty-offset*8,"Trial Hit Fraction:  ",fontsize=fs,horizontalalignment='right');   fig.text(.7,starty-offset*8,str(round(get_trial_hit_fraction(fit),2)),fontsize=fs)
    fig.text(.7,starty-offset*9,"Dropout Task/Timing Index:  " ,fontsize=fs,horizontalalignment='right');   fig.text(.7,starty-offset*9,str(round(get_timing_index_fit(fit),2)),fontsize=fs) 
    fig.text(.7,starty-offset*10,"Weight Task/Timing Index:  " ,fontsize=fs,horizontalalignment='right');   fig.text(.7,starty-offset*10,str(round(get_weight_timing_index_fit(fit),2)),fontsize=fs)  
    fig.text(.7,starty-offset*11,"Num Hits:  " ,fontsize=fs,horizontalalignment='right');                   fig.text(.7,starty-offset*11,np.sum(fit['psydata']['hits']),fontsize=fs)  
    plt.tight_layout()
    #plt.subplots_adjust(right=0.8)
    if savefig:
        filename = directory + str(fit['ID'])+"_summary.png"
        plt.savefig(filename)
    

def plot_fit(ID, cluster_labels=None,fit=None, directory=None,validation=True,savefig=False,num_clusters=None):
    '''
        Plots the fit associated with a session ID
        Needs the fit dictionary. If you pass these values into, the function is much faster 
    '''
    if type(directory) == type(None):
        directory = global_directory
    if not (type(fit) == type(dict())):
        fit = load_fit(ID, directory=directory)
    if savefig:
        filename = directory + str(ID)
    else:
        filename=None
    plot_weights(fit['wMode'], fit['weights'],fit['psydata'],errorbar=fit['credibleInt'], ypred = fit['ypred'],cluster_labels=cluster_labels,validation=validation,filename=filename,num_clusters=num_clusters)
    summarize_fit(fit,directory=directory, savefig=savefig)
    return fit
   
def cluster_fit(fit,directory=None,minC=2,maxC=4):
    '''
        Given a fit performs a series of clustering, adds the results to the fit dictionary, and saves the results to a pkl file
    '''
    if type(directory) == type(None):
        directory = global_directory
    numc= range(minC,maxC+1)
    cluster = dict()
    for i in numc:
        output = cluster_weights(fit['wMode'],i)
        cluster[str(i)] = output
    fit['cluster'] = cluster
    filename = directory + str(fit['ID']) + "_clusters.pkl" 
    save(filename, cluster) 
    return fit

def cluster_weights(wMode,num_clusters):
    '''
        Clusters the weights in wMode into num_clusters clusters
    '''
    output = k_means(transform(wMode.T),num_clusters)
    return output

def check_clustering(wMode,numC=5):
    '''
        For a set of weights (regressors x time points), computes a series of clusterings from 1 up to numC clusters
        Plots the weights and the cluster labelings
        
        Returns the scores for each clustering
    '''
    fig,ax = plt.subplots(nrows=numC,ncols=1)
    scores = []
    for j in range(0,numC):
        for i in range(0,4):
            ax[j].plot(transform(wMode[i,:]))
        output = cluster_weights(wMode,j+1)
        cp = np.where(~(np.diff(output[1]) == 0))[0]
        cp = np.concatenate([[0], cp, [len(output[1])]])
        colors = ['r','b','g','c','m','k','y']
        for i in range(0, len(cp)-1):
            ax[j].axvspan(cp[i],cp[i+1],color=colors[output[1][cp[i]+1]], alpha=0.1)
        ax[j].set_ylim(0,1)
        ax[j].set_xlim(0,len(wMode[0,:]))
        ax[j].set_ylabel(str(j+2)+" clusters")
        ax[j].set_xlabel('Flash #')
        scores.append(output[2])
    return scores

def check_all_clusters(IDS, numC=8):
    '''
        For each session in IDS, performs clustering from 1 cluster up to numC clusters
        Plots the normalized error (euclidean distance from each point to each cluster center) for each cluster-number
    '''
    all_scores = []
    for i in IDS:
        scores = []
        try:
            wMode = get_all_weights([i])
        except:
            pass
        else:
            if not (type(wMode) == type(None)):
                for j in range(0,numC):
                    output = cluster_weights(wMode,j+1)
                    scores.append(output[2])
                all_scores.append(scores)
    
    plt.figure()
    for i in np.arange(0,len(all_scores)):
        plt.plot(np.arange(1,j+2), all_scores[i]/all_scores[i][0],'k-',alpha=0.3)    
    plt.ylabel('Normalized error')
    plt.xlabel('number of clusters')
    

def load_mouse(mouse, get_behavior=False):
    '''
        Takes a mouse donor_id, returns a list of all sessions objects, their IDS, and whether it was active or not. 
        if get_behavior, returns all BehaviorSessions
        no matter what, always returns the behavior_session_id for each session. 
        if global OPHYS, then forces get_behavior=False
    '''
    return pgt.load_mouse(mouse, get_behavior=get_behavior)

def format_mouse(sessions,IDS,format_options={}):
    '''
        Takes a list of sessions and returns a list of psydata formatted dictionaries for each session, and IDS a list of the IDS that go into each session
    '''
    d =[]
    good_ids =[]
    for session, id in zip(sessions,IDS):
        try:
            pm.annotate_licks(session) 
            pm.annotate_bouts(session)
            psydata = format_session(session,format_options)
        except Exception as e:
            print(str(id) +" "+ str(e))
        else:
            print(str(id))
            d.append(psydata)
            good_ids.append(id)
    return d, good_ids

def merge_datas(psydatas):
    ''' 
        Takes a list of psydata dictionaries and concatenates them into one master dictionary. Computes the dayLength field to keep track of where day-breaks are
        Also records the session_label for each dictionary
    '''
    if len(psydatas) == 0:
        raise Exception('No data to merge')
    if len(psydatas) == 1:
        print('Only one session, no need to merge')
        psydata = psydatas[0]
        return psydata
    else:
        print('Merging ' + str(len(psydatas)) + ' sessions')
    psydata = copy.deepcopy(psydatas[0])
    psydata['dayLength'] = [len(psydatas[0]['y'])]
    for d in psydatas[1:]:    
        psydata['y'] = np.concatenate([psydata['y'], d['y']])
        for key in psydata['inputs'].keys():
            psydata['inputs'][key] = np.concatenate([psydata['inputs'][key], d['inputs'][key]])

        psydata['false_alarms'] = np.concatenate([psydata['false_alarms'], d['false_alarms']])
        psydata['correct_reject'] = np.concatenate([psydata['correct_reject'], d['correct_reject']])
        psydata['hits'] = np.concatenate([psydata['hits'], d['hits']])
        psydata['misses'] = np.concatenate([psydata['misses'], d['misses']])
        psydata['aborts'] = np.concatenate([psydata['aborts'], d['aborts']])
        psydata['auto_rewards'] = np.concatenate([psydata['auto_rewards'], d['auto_rewards']])
        psydata['start_times'] = np.concatenate([psydata['start_times'], d['start_times']])
        psydata['session_label']= np.concatenate([psydata['session_label'], d['session_label']])
        psydata['dayLength'] = np.concatenate([psydata['dayLength'], [len(d['y'])]])
        psydata['flash_ids'] = np.concatenate([psydata['flash_ids'],d['flash_ids']])
        psydata['df'] = pd.concat([psydata['df'], d['df']])

    psydata['dayLength'] = np.array(psydata['dayLength'])
    return psydata


def process_mouse(donor_id,directory=None,format_options={}):
    '''
        Takes a mouse donor_id, loads all ophys_sessions, and fits the model in the temporal order in which the data was created.
    '''
    if type(directory) == type(None):
        print('Couldnt find directory, using global')
        directory = global_directory

    filename = directory + 'mouse_' + str(donor_id) 
    print(filename)

    if os.path.isfile(filename+".pkl"):
        print('Already completed this fit, quitting')
        return

    print('Building List of Sessions and pulling')
    sessions, all_IDS,active = load_mouse(donor_id) # sorts the sessions by time
    print('Got  ' + str(len(all_IDS)) + ' sessions')
    print("Formating Data")
    psydatas, good_IDS = format_mouse(np.array(sessions)[active],np.array(all_IDS)[active],format_options={})
    print('Got  ' + str(len(good_IDS)) + ' good sessions')
    print("Merging Formatted Sessions")
    psydata = merge_datas(psydatas)

    print("Initial Fit")    
    hyp, evd, wMode, hess, credibleInt,weights = fit_weights(psydata,OMISSIONS=True)
    ypred,ypred_each = compute_ypred(psydata, wMode,weights)
    plot_weights(wMode, weights,psydata,errorbar=credibleInt, ypred = ypred,filename=filename, session_labels = psydata['session_label'])

    print("Cross Validation Analysis")
    cross_results = compute_cross_validation(psydata, hyp, weights,folds=10)
    cv_pred = compute_cross_validation_ypred(psydata, cross_results,ypred)

    metadata =[]
    for s in sessions:
        try:
            m = s.metadata
        except:
            m = []
        metadata.append(m)

    labels = ['hyp', 'evd', 'wMode', 'hess', 'credibleInt', 'weights', 'ypred','psydata','good_IDS','metadata','all_IDS','active','cross_results','cv_pred','mouse_ID']
    output = [hyp, evd, wMode, hess, credibleInt, weights, ypred,psydata,good_IDS,metadata,all_IDS,active,cross_results,cv_pred,donor_id]
    fit = dict((x,y) for x,y in zip(labels, output))
   
    print("Clustering Behavioral Epochs")
    fit = cluster_mouse_fit(fit,directory=directory)

    save(filename+".pkl", fit)
    plt.close('all')

def get_good_behavior_IDS(IDS,hit_threshold=100):
    '''
        Filters all the ids in IDS for sessions with greather than hit_threshold hits
        Returns a list of session ids
    '''
    good_ids = []
    for id in IDS:
        try:
            summary = get_session_summary(id)
        except:
            pass
        else:
            if np.sum(summary[7]['psydata']['hits']) > hit_threshold:
                good_ids.append(id)
    return good_ids

def compute_model_prediction_correlation(fit,fit_mov=50,data_mov=50,plot_this=False,cross_validation=True):
    '''
        Computes the R^2 value between the model predicted licking probability, and the smoothed data lick rate.
        The data is smoothed over data_mov flashes. The model is smoothed over fit_mov flashes. Both smoothings uses a moving _mean within that range. 
        if plot_this, then the two smoothed traces are plotted
        if cross_validation, then uses the cross validated model prediction, and not the training set predictions
        Returns, the r^2 value.
    '''
    if cross_validation:
        data = copy.copy(fit['psydata']['y']-1)
        model = copy.copy(fit['cv_pred'])
    else:
        data = copy.copy(fit['psydata']['y']-1)
        model = copy.copy(fit['ypred'])
    data_smooth = pgt.moving_mean(data,data_mov)
    ypred_smooth = pgt.moving_mean(model,fit_mov)

    minlen = np.min([len(data_smooth), len(ypred_smooth)])
    if plot_this:
        plt.figure()
        plt.plot(ypred_smooth, 'k')
        plt.plot(data_smooth,'b')
    return round(np.corrcoef(ypred_smooth[0:minlen], data_smooth[0:minlen])[0,1]**2,2)

def compute_model_roc(fit,plot_this=False,cross_validation=True):
    '''
        Computes area under the ROC curve for the model in fit. If plot_this, then plots the ROC curve. 
        If cross_validation, then uses the cross validated prediction in fit, not he training fit.
        Returns the AU. ROC single float
    '''
    if cross_validation:
        data = copy.copy(fit['psydata']['y']-1)
        model = copy.copy(fit['cv_pred'])
    else:
        data = copy.copy(fit['psydata']['y']-1)
        model = copy.copy(fit['ypred'])

    if plot_this:
        plt.figure()
        alarms,hits,thresholds = roc_curve(data,model)
        plt.plot(alarms,hits,'ko-')
        plt.plot([0,1],[0,1],'k--')
        plt.ylabel('Hits')
        plt.xlabel('False Alarms')
    return roc_auc_score(data,model)

def plot_session_summary_roc(IDS,directory=None,savefig=False,group_label="",verbose=True,cross_validation=True,fs1=12,fs2=12,filetype=".png"):
    '''
        Make a summary plot of the histogram of AU.ROC values for all sessions in IDS.
    '''
    if type(directory) == type(None):
        directory = global_directory
    # make figure    
    fig,ax = plt.subplots(figsize=(5,4))
    scores = []
    ids = []
    counter = 0
    hits = []
    for id in IDS:
        try:
            session_summary = get_session_summary(id,directory=directory)
        except:
            pass
        else:
            fit = session_summary[7]
            roc = compute_model_roc(fit,plot_this=False,cross_validation=cross_validation)
            scores.append(roc)
            ids.append(id)
            hits.append(np.sum(fit['psydata']['hits']))
            counter +=1

    if counter == 0:
        print('NO DATA')
        return
    ax.set_xlim(0.5,1)
    ax.hist(np.array(scores),bins=25)
    ax.set_ylabel('Count', fontsize=fs1)
    ax.set_xlabel('ROC-AUC', fontsize=fs1)
    ax.xaxis.set_tick_params(labelsize=fs2)
    ax.yaxis.set_tick_params(labelsize=fs2)
    meanscore = np.median(np.array(scores))
    ax.plot(meanscore, ax.get_ylim()[1],'rv')
    ax.axvline(meanscore,color='r', alpha=0.3)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"roc"+filetype)
    if verbose:
        median = np.argsort(np.array(scores))[len(scores)//2]
        best = np.argmax(np.array(scores))
        worst = np.argmin(np.array(scores)) 
        print("ROC Summary:")
        print('Worst  Session: ' + str(ids[worst]) + " " + str(scores[worst]))
        print('Median Session: ' + str(ids[median]) + " " + str(scores[median]))
        print('Best   Session: ' + str(ids[best]) + " " + str(scores[best]))     

    plt.figure()
    plt.plot(scores, hits, 'ko')
    plt.xlim(0.5,1)
    plt.ylim(0,200)
    plt.ylabel('Hits',fontsize=12)
    plt.xlabel('ROC-AUC',fontsize=12)
    plt.gca().xaxis.set_tick_params(labelsize=12)
    plt.gca().yaxis.set_tick_params(labelsize=12)    
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_"+group_label+"roc_vs_hits"+filetype)
    return scores, ids 

def load_mouse_fit(ID, directory=None):
    '''
        Loads the fit for session ID, in directory
        Creates a dictionary for the session
        if the fit has cluster labels then it loads them and puts them into the dictionary
    '''
    if type(directory) == type(None):
        directory = global_directory

    filename = directory + "mouse_"+ str(ID) + ".pkl" 
    fit = load(filename)
    fit['mouse_ID'] = ID
    #if os.path.isfile(directory+"mouse_"+str(ID) + "_clusters.pkl"):
    #    clusters = load(directory+"mouse_"+str(ID) + "_clusters.pkl")
    #    fit['clusters'] = clusters
    #else:
    #    fit = cluster_mouse_fit(fit,directory=directory)
    return fit


def cluster_mouse_fit(fit,directory=None,minC=2,maxC=4):
    '''
        Given a fit performs a series of clustering, adds the results to the fit dictionary, and saves the results to a pkl file
    '''
    if type(directory) == type(None):
        directory = global_directory

    numc= range(minC,maxC+1)
    cluster = dict()
    for i in numc:
        output = cluster_weights(fit['wMode'],i)
        cluster[str(i)] = output
    fit['cluster'] = cluster
    filename = directory + "mouse_" + str(fit['mouse_ID']) + "_clusters.pkl" 
    save(filename, cluster) 
    return fit

def plot_mouse_fit(ID, cluster_labels=None, fit=None, directory=None,validation=True,savefig=False):
    '''
        Plots the fit associated with a session ID
        Needs the fit dictionary. If you pass these values into, the function is much faster 
    '''
    if type(directory) == type(None):
        directory = global_directory

    if not (type(fit) == type(dict())):
        fit = load_mouse_fit(ID, directory=directory)
    if savefig:
        filename = directory + 'mouse_' + str(ID) 
    else:
        filename=None
    plot_weights(fit['wMode'], fit['weights'],fit['psydata'],errorbar=fit['credibleInt'], ypred = fit['ypred'],cluster_labels=cluster_labels,validation=validation,filename=filename,session_labels=fit['psydata']['session_label'])
    return fit

def get_all_fit_weights(ids,directory=None):
    '''
        Returns a list of all the regression weights for the sessions in IDS
        
        INPUTS:
        ids, a list of sessions
        
        OUTPUTS:
        w, a list of the weights in each session
        w_ids, the ids that loaded and have weights in w
    '''
    w = []
    w_ids = []
    crashed = 0
    for id in ids:
        try:
            fit = load_fit(id,directory)
            w.append(fit['wMode'])
            w_ids.append(id)
        except:
            print(str(id)+" crash")
            crashed+=1
            pass
    print(str(crashed) +" crashed sessions")
    return w, w_ids

def merge_weights(w): 
    '''
        Merges a list of weights into one long array of weights
    '''
    return np.concatenate(w,axis=1)           

def cluster_all(w,minC=2, maxC=4,directory=None,save_results=False):
    '''
        Clusters the weights in array w. Uses the cluster_weights function
        
        INPUTS:
        w, an array of weights
        minC, the smallest number of clusters to try
        maxC, the largest number of clusters to try
        directory, where to save the results
    
        OUTPUTS:
        cluster, the output from cluster_weights
        
        SAVES:
        the cluster results in 'all_clusters.pkl'
    '''
    if type(directory) == type(None):
        directory = global_directory

    numc= range(minC,maxC+1)
    cluster = dict()
    for i in numc:
        output = cluster_weights(w,i)
        cluster[str(i)] = output
    if save_results:
        filename = directory + "all_clusters.pkl" 
        save(filename, cluster) 
    return cluster

def unmerge_cluster(cluster,w,w_ids,directory=None,save_results=False):
    '''
        Unmerges an array of weights and clustering results into a list for each session
        
        INPUTS:
        cluster, the clustering results from cluster_all
        w, an array of weights
        w_ids, the list of ids which went into w
    
        outputs,
        session_clusters, a list of cluster results on a session by session basis
    '''
    session_clusters = dict()
    counter = 0
    for weights, id in zip(w,w_ids):
        session_clusters[id] = dict()
        start = counter
        end = start + np.shape(weights)[1]
        for key in cluster.keys():
            session_clusters[id][key] =(cluster[key][0],cluster[key][1][start:end],cluster[key][2]) 
        counter = end
    if save_results:
        save_session_clusters(session_clusters,directory=directory)
        save_all_clusters(w_ids,session_clusters,directory=directory)
    return session_clusters

def save_session_clusters(session_clusters, directory=None):
    '''
        Saves the session_clusters in 'session_clusters,pkl'

    '''
    if type(directory) == type(None):
        directory = global_directory

    filename = directory + "session_clusters.pkl"
    save(filename,session_clusters)

def save_all_clusters(w_ids,session_clusters, directory=None):
    '''
        Saves each sessions all_clusters
    '''
    if type(directory) == type(None):
        directory = global_directory

    for key in session_clusters.keys():
        filename = directory + str(key) + "_all_clusters.pkl" 
        save(filename, session_clusters[key]) 

def build_all_clusters(ids,directory=None,save_results=False):
    '''
        Clusters all the sessions in IDS jointly
    '''
    if type(directory) == type(None):
        directory = global_directory
    w,w_ids = get_all_fit_weights(ids,directory=directory)
    w_all = merge_weights(w)
    cluster = cluster_all(w_all,directory=directory,save_results=save_results)
    session_clusters= unmerge_cluster(cluster,w,w_ids,directory=directory,save_results=save_results)

def check_session(ID, directory=None):
    '''
        Checks if the ID has a model fit computed
    '''
    if type(directory) == type(None):
        directory = global_directory

    filename = directory + str(ID) + ".pkl" 
    has_fit =  os.path.isfile(filename)

    if has_fit:
        print("Session has a fit, load the results with load_fit(ID)")
    else:
        print("Session does not have a fit, fit the session with process_session(ID)")
    return has_fit

def get_all_dropout(IDS,directory=None,hit_threshold=50,verbose=False): 
    '''
        For each session in IDS, returns the vector of dropout scores for each model
    '''
    # Add to big matr
    if type(directory) == type(None):
        directory = global_directory
    all_dropouts = []
    hits = []
    false_alarms = []
    misses = []
    ids = []
    crashed = 0
    # Loop through IDS
    for id in tqdm(IDS):
        try:
            fit = load_fit(id,directory=directory)
            if np.sum(fit['psydata']['hits']) > hit_threshold:
                dropout = get_session_dropout(fit)
                all_dropouts.append(dropout)
                hits.append(np.sum(fit['psydata']['hits']))
                false_alarms.append(np.sum(fit['psydata']['false_alarms']))
                misses.append(np.sum(fit['psydata']['misses']))
                ids.append(id)
        except:
            if verbose:
                print(str(id) +" crash")
            crashed +=1
    print(str(crashed) + " crashed")
    dropouts = np.stack(all_dropouts,axis=1)
    filepath = directory + "all_dropouts.pkl"
    save(filepath, dropouts)
    return dropouts,hits, false_alarms, misses,ids

def load_all_dropout(directory=None):
    dropout = load(directory+"all_dropouts.pkl")
    return dropout


def get_mice_weights(mice_ids,directory=None,hit_threshold=50,verbose=False):
    if type(directory) == type(None):
        directory = global_directory
    mice_weights = []
    mice_good_ids = []
    crashed = 0
    low_hits = 0
    # Loop through IDS
    for id in tqdm(mice_ids):
        this_mouse = []
        for sess in np.intersect1d(pgt.get_mice_sessions(id),pgt.get_active_ids()):
            try:
                fit = load_fit(sess,directory=directory)
                if np.sum(fit['psydata']['hits']) > hit_threshold:
                    this_mouse.append(np.mean(fit['wMode'],1))
                else:
                    low_hits +=1
            except:
                if verbose:
                    print("Mouse: "+str(id)+" session: "+str(sess) +" crash")
                crashed += 1
        if len(this_mouse) > 0:
            this_mouse = np.stack(this_mouse,axis=1)
            mice_weights.append(this_mouse)
            mice_good_ids.append(id)
    print()
    print(str(crashed) + " crashed")
    print(str(low_hits) + " below hit_threshold")
    return mice_weights,mice_good_ids

def get_mice_dropout(mice_ids,directory=None,hit_threshold=50,verbose=False):
    if type(directory) == type(None):
        directory = global_directory
    mice_dropouts = []
    mice_good_ids = []
    crashed = 0
    low_hits = 0
    # Loop through IDS
    for id in tqdm(mice_ids):
        this_mouse = []
        for sess in np.intersect1d(pgt.get_mice_sessions(id),pgt.get_active_ids()):
            try:
                fit = load_fit(sess,directory=directory)
                if np.sum(fit['psydata']['hits']) > hit_threshold:
                    dropout = get_session_dropout(fit)
                    this_mouse.append(dropout)
                else:
                    low_hits +=1
            except:
                if verbose:
                    print("Mouse: "+str(id)+" Session:"+str(sess)+" crash")
                crashed +=1
        if len(this_mouse) > 0:
            this_mouse = np.stack(this_mouse,axis=1)
            mice_dropouts.append(this_mouse)
            mice_good_ids.append(id)
    print()
    print(str(crashed) + " crashed")
    print(str(low_hits) + " below hit_threshold")
    return mice_dropouts,mice_good_ids

def PCA_dropout(ids,mice_ids,dir,verbose=False):
    dropouts, hits,false_alarms,misses,ids = get_all_dropout(ids,directory=dir,verbose=verbose)
    mice_dropouts, mice_good_ids = get_mice_dropout(mice_ids,directory=dir,verbose=verbose)
    fit = load_fit(ids[1],directory=dir)
    pca,dropout_dex,varexpl = PCA_on_dropout(dropouts, labels=fit['labels'], mice_dropouts=mice_dropouts,mice_ids=mice_good_ids, hits=hits,false_alarms=false_alarms, misses=misses,directory=dir)
    return dropout_dex,varexpl

def PCA_on_dropout(dropouts,labels=None,mice_dropouts=None, mice_ids = None,hits=None,false_alarms=None, misses=None,directory=None,fs1=12,fs2=12,filetype='.png',ms=2):
    # get labels from fit['labels'] for random session
    # mice_dropouts, mice_good_ids = ps.get_mice_dropout(ps.get_mice_ids())
    # dropouts = ps.load_all_dropout()
    if type(directory) == type(None):
        directory = global_directory   
    if directory[-2] == '2':
        sdex = 2
        edex = 16
    elif directory[-2] == '4':
        sdex = 2
        edex = 18
    elif directory[-2] == '6':
        sdex = 2 
        edex = 6
    elif directory[-2] == '7':
        sdex = 2 
        edex = 6
    elif directory[-2] == '8':
        sdex = 2 
        edex = 6
    elif directory[-2] == '9':
        sdex = 2 
        edex = 6
    dex = -(dropouts[sdex,:] - dropouts[edex,:])
    pca = PCA()
    
    # Removing Bias from PCA
    dropouts = dropouts[2:,:]
    labels = labels[2:]

    pca.fit(dropouts.T)
    X = pca.transform(dropouts.T)
    #plt.figure(figsize=(4,2.9))
    fig,ax = plt.subplots(figsize=(6,4.5))
    fig=plt.gcf()
    ax = [plt.gca()]
    scat = ax[0].scatter(-X[:,0], X[:,1],c=dex,cmap='plasma')
    cbar = fig.colorbar(scat, ax = ax[0])
    cbar.ax.set_ylabel('Task Dropout Index',fontsize=fs2)
    ax[0].set_xlabel('Dropout PC 1',fontsize=fs1)
    ax[0].set_ylabel('Dropout PC 2',fontsize=fs1)
    ax[0].axis('equal')
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.tight_layout()   
    plt.savefig(directory+"dropout_pca"+filetype)
 
    plt.figure(figsize=(6,3))
    fig=plt.gcf()
    ax.append(plt.gca())
    ax[1].axhline(0,color='k',alpha=0.2)
    for i in np.arange(0,len(dropouts)):
        if np.mod(i,2) == 0:
            ax[1].axvspan(i-.5,i+.5,color='k', alpha=0.1)
    pca1varexp = str(100*round(pca.explained_variance_ratio_[0],2))
    pca2varexp = str(100*round(pca.explained_variance_ratio_[1],2))
    ax[1].plot(-pca.components_[0,:],'ko-',label='PC1 '+pca1varexp+"%")
    ax[1].plot(-pca.components_[1,:],'ro-',label='PC2 '+pca2varexp+"%")
    ax[1].set_xlabel('Model Component',fontsize=12)
    ax[1].set_ylabel('% change in \n evidence',fontsize=12)
    ax[1].tick_params(axis='both',labelsize=10)
    ax[1].set_xticks(np.arange(0,len(dropouts)))
    if type(labels) is not type(None):    
        ax[1].set_xticklabels(labels,rotation=90)
    ax[1].legend()
    plt.tight_layout()
    plt.savefig(directory+"dropout_pca_1.png")

    plt.figure(figsize=(5,4.5))
    scat = plt.gca().scatter(-X[:,0],dex,c=dex,cmap='plasma')
    #cbar = plt.gcf().colorbar(scat, ax = plt.gca())
    #cbar.ax.set_ylabel('Task Dropout Index',fontsize=fs1)
    plt.gca().set_xlabel('Dropout PC 1',fontsize=fs1)
    plt.gca().set_ylabel('Task Dropout Index',fontsize=fs1)   
    plt.gca().axis('equal')
    plt.gca().tick_params(axis='both',labelsize=10)
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.tight_layout()
    plt.savefig(directory+"dropout_pca_3"+filetype)

    plt.figure(figsize=(5,4.5))
    ax = plt.gca()
    if type(mice_dropouts) is not type(None):
        ax.axhline(0,color='k',alpha=0.2)
        ax.set_xlabel('Individual Mice', fontsize=fs1)
        ax.set_ylabel('Task Dropout Index', fontsize=fs1)
        ax.set_xticks(range(0,len(mice_dropouts)))
        ax.set_ylim(-45,30)
        mean_drop = []
        for i in range(0, len(mice_dropouts)):
            mean_drop.append(-1*np.nanmean(mice_dropouts[i][sdex,:]-mice_dropouts[i][edex,:]))
        sortdex = np.argsort(np.array(mean_drop))
        mice_dropouts = [mice_dropouts[i] for i in sortdex]
        mean_drop = np.array(mean_drop)[sortdex]
        for i in range(0,len(mice_dropouts)):
            if np.mod(i,2) == 0:
                ax.axvspan(i-.5,i+.5,color='k', alpha=0.1)
            mouse_dex = -(mice_dropouts[i][sdex,:]-mice_dropouts[i][edex,:])
            ax.plot([i-0.5, i+0.5], [mean_drop[i],mean_drop[i]], 'k-',alpha=0.3)
            ax.scatter(i*np.ones(np.shape(mouse_dex)), mouse_dex,ms,c=mouse_dex,cmap='plasma',vmin=(dex).min(),vmax=(dex).max(),alpha=1)
        sorted_mice_ids = ["" for i in sortdex]
        ax.set_xticklabels(sorted_mice_ids,{'fontsize':10},rotation=90)
    plt.tight_layout()
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.xlim(-1,55)
    plt.savefig(directory+"dropout_pca_mice"+filetype)

    plt.figure(figsize=(5,4.5))
    ax = plt.gca()   
    ax.plot(pca.explained_variance_ratio_*100,'ko-')
    ax.set_xlabel('PC Dimension',fontsize=fs1)
    ax.set_ylabel('Explained Variance %',fontsize=fs1)
    plt.tight_layout()
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.savefig(directory+"dropout_pca_var_expl"+filetype)

    fig, ax = plt.subplots(2,3,figsize=(10,6))
    #ax[0,0].axhline(0,color='k',alpha=0.2)
    #ax[0,0].axvline(0,color='k',alpha=0.2)
    ax[0,0].scatter(-X[:,0], dex,c=dex,cmap='plasma')
    ax[0,0].set_xlabel('Dropout PC 1',fontsize=fs2)
    ax[0,0].set_ylabel('Task Dropout Index',fontsize=fs2)
    ax[0,1].plot(pca.explained_variance_ratio_*100,'ko-')
    ax[0,1].set_xlabel('PC Dimension',fontsize=fs2)
    ax[0,1].set_ylabel('Explained Variance %',fontsize=fs2)

    if type(mice_dropouts) is not type(None):
        ax[1,0].axhline(0,color='k',alpha=0.2)
        ax[1,0].set_ylabel('Task Dropout Index', fontsize=12)
        ax[1,0].set_xticks(range(0,len(mice_dropouts)))
        ax[1,0].set_ylim(-45,30)
        mean_drop = []
        for i in range(0, len(mice_dropouts)):
            mean_drop.append(-1*np.nanmean(mice_dropouts[i][sdex,:]-mice_dropouts[i][edex,:]))
        sortdex = np.argsort(np.array(mean_drop))
        mice_dropouts = [mice_dropouts[i] for i in sortdex]
        mean_drop = np.array(mean_drop)[sortdex]
        for i in range(0,len(mice_dropouts)):
            if np.mod(i,2) == 0:
                ax[1,0].axvspan(i-.5,i+.5,color='k', alpha=0.1)
            mouse_dex = -(mice_dropouts[i][sdex,:]-mice_dropouts[i][edex,:])
            ax[1,0].plot([i-0.5, i+0.5], [mean_drop[i],mean_drop[i]], 'k-',alpha=0.3)
            ax[1,0].scatter(i*np.ones(np.shape(mouse_dex)), mouse_dex,c=mouse_dex,cmap='plasma',vmin=(dex).min(),vmax=(dex).max(),alpha=1)
        sorted_mice_ids = [mice_ids[i] for i in sortdex]
        ax[1,0].set_xticklabels(sorted_mice_ids,{'fontsize':10},rotation=90)
    if type(hits) is not type(None):
        ax[1,1].scatter(dex, hits,c=dex,cmap='plasma')
        ax[1,1].set_ylabel('Hits/session',fontsize=12)
        ax[1,1].set_xlabel('Task Dropout Index',fontsize=12)
        ax[1,1].axvline(0,color='k',alpha=0.2)
        ax[1,1].set_xlim(-45,30)
        ax[1,1].set_ylim(bottom=0)

        ax[0,2].scatter(dex, false_alarms,c=dex,cmap='plasma')
        ax[0,2].set_ylabel('FA/session',fontsize=12)
        ax[0,2].set_xlabel('Task Dropout Index',fontsize=12)
        ax[0,2].axvline(0,color='k',alpha=0.2)
        ax[0,2].set_xlim(-45,30)
        ax[0,2].set_ylim(bottom=0)


        ax[1,2].scatter(dex, misses,c=dex,cmap='plasma')
        ax[1,2].set_ylabel('Miss/session',fontsize=12)
        ax[1,2].set_xlabel('Task Dropout Index',fontsize=12)
        ax[1,2].axvline(0,color='k',alpha=0.2)
        ax[1,2].set_xlim(-45,30)
        ax[1,2].set_ylim(bottom=0)
    plt.tight_layout()
    plt.savefig(directory+"dropout_pca_2.png")

    plt.figure(figsize=(5,4.5))
    ax = plt.gca() 
    ax.scatter(dex, hits,c=dex,cmap='plasma')
    ax.set_ylabel('Hits/session',fontsize=fs1)
    ax.set_xlabel('Task Dropout Index',fontsize=fs1)
    ax.axvline(0,color='k',alpha=0.2)
    ax.set_xlim(-45,30)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.savefig(directory+"dropout_pca_hits"+filetype)


    plt.figure(figsize=(5,4.5))
    ax = plt.gca()
    ax.scatter(dex, false_alarms,c=dex,cmap='plasma')
    ax.set_ylabel('FA/session',fontsize=fs1)
    ax.set_xlabel('Task Dropout Index',fontsize=fs1)
    ax.axvline(0,color='k',alpha=0.2)
    ax.set_xlim(-45,30)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.savefig(directory+"dropout_pca_fa"+filetype)



    plt.figure(figsize=(5,4.5))
    ax = plt.gca() 
    ax.scatter(dex, misses,c=dex,cmap='plasma')
    ax.set_ylabel('Miss/session',fontsize=fs1)
    ax.set_xlabel('Task Dropout Index',fontsize=fs1)
    ax.axvline(0,color='k',alpha=0.2)
    ax.set_xlim(-45,30)
    ax.set_ylim(bottom=0)
    plt.tight_layout()
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.savefig(directory+"dropout_pca_miss"+filetype)

    varexpl = 100*round(pca.explained_variance_ratio_[0],2)
    return pca,dex,varexpl

def PCA_weights(ids,mice_ids,directory,verbose=False):
    all_weights =plot_session_summary_weights(ids,return_weights=True,directory=directory)
    x = np.vstack(all_weights)
    task = x[:,2]
    timing = x[:,3]
    dex = task-timing
    pca = PCA()
    pca.fit(x)
    X = pca.transform(x)
    plt.figure(figsize=(4,2.9))
    scat = plt.gca().scatter(X[:,0],X[:,1],c=dex,cmap='plasma')
    cbar = plt.gcf().colorbar(scat, ax = plt.gca())
    cbar.ax.set_ylabel('Task Weight Index',fontsize=12)
    plt.gca().set_xlabel('Weight PC 1 - '+str(100*round(pca.explained_variance_ratio_[0],2))+"%",fontsize=12)
    plt.gca().set_ylabel('Weight PC 2 - '+str(100*round(pca.explained_variance_ratio_[1],2))+"%",fontsize=12)
    plt.gca().axis('equal')   
    plt.gca().tick_params(axis='both',labelsize=10)
    plt.tight_layout()
    plt.savefig(directory+"weight_pca_1.png")

    plt.figure(figsize=(4,2.9))
    scat = plt.gca().scatter(X[:,0],dex,c=dex,cmap='plasma')
    cbar = plt.gcf().colorbar(scat, ax = plt.gca())
    cbar.ax.set_ylabel('Task Weight Index',fontsize=12)
    plt.gca().set_xlabel('Weight PC 1',fontsize=12)
    plt.gca().set_ylabel('Task Weight Index',fontsize=12)
    plt.gca().axis('equal')
    plt.gca().tick_params(axis='both',labelsize=10)
    plt.tight_layout()
    plt.savefig(directory+"weight_pca_2.png")   

    plt.figure(figsize=(6,3))
    fig=plt.gcf()
    ax =plt.gca()
    ax.axhline(0,color='k',alpha=0.2)
    for i in np.arange(0,np.shape(x)[1]):
        if np.mod(i,2) == 0:
            ax.axvspan(i-.5,i+.5,color='k', alpha=0.1)
    pca1varexp = str(100*round(pca.explained_variance_ratio_[0],2))
    pca2varexp = str(100*round(pca.explained_variance_ratio_[1],2))
    ax.plot(pca.components_[0,:],'ko-',label='PC1 '+pca1varexp+"%")
    ax.plot(pca.components_[1,:],'ro-',label='PC2 '+pca2varexp+"%")
    ax.set_xlabel('Model Component',fontsize=12)
    ax.set_ylabel('Avg Weight',fontsize=12)
    ax.tick_params(axis='both',labelsize=10)
    ax.set_xticks(np.arange(0,np.shape(x)[1]))
    fit = load_fit(ids[0],directory=directory)
    weights_list = get_weights_list(fit['weights'])
    labels = clean_weights(weights_list)    
    ax.set_xticklabels(labels,rotation=90)
    ax.legend()
    plt.tight_layout()
    plt.savefig(directory+"weight_pca_3.png")

    _, hits,false_alarms,misses,ids = get_all_dropout(ids,directory=directory,verbose=verbose)
    mice_weights, mice_good_ids = get_mice_weights(mice_ids, directory=directory,verbose=verbose)

    fig, ax = plt.subplots(2,3,figsize=(10,6))
    ax[0,0].scatter(X[:,0], dex,c=dex,cmap='plasma')
    ax[0,0].set_xlabel('Weight PC 1',fontsize=12)
    ax[0,0].set_ylabel('Task Weight Index',fontsize=12)
    ax[0,1].plot(pca.explained_variance_ratio_*100,'ko-')
    ax[0,1].set_xlabel('PC Dimension',fontsize=12)
    ax[0,1].set_ylabel('Explained Variance %',fontsize=12)

    ax[1,0].axhline(0,color='k',alpha=0.2)
    ax[1,0].set_ylabel('Task Weight Index', fontsize=12)
    ax[1,0].set_xticks(range(0,len(mice_good_ids)))
    ax[1,0].set_ylim(-6,6)
    mean_weight = []
    for i in range(0, len(mice_good_ids)):
        this_weight = np.mean(mice_weights[i],1)
        mean_weight.append(this_weight[2] -this_weight[3])
    sortdex = np.argsort(np.array(mean_weight))
    mice_weights_sorted = [mice_weights[i] for i in sortdex]
    mean_weight = np.array(mean_weight)[sortdex]
    for i in range(0,len(mice_good_ids)):
        if np.mod(i,2) == 0:
            ax[1,0].axvspan(i-.5,i+.5,color='k', alpha=0.1)
        this_mouse_weights = mice_weights_sorted[i][2,:] - mice_weights_sorted[i][3,:]
        ax[1,0].plot([i-0.5,i+0.5],[mean_weight[i],mean_weight[i]],'k-',alpha=0.3)
        ax[1,0].scatter(i*np.ones(np.shape(this_mouse_weights)), this_mouse_weights,c=this_mouse_weights,cmap='plasma',vmin=(dex).min(),vmax=(dex).max(),alpha=1)
    sorted_mice_ids = [mice_good_ids[i] for i in sortdex]
    ax[1,0].set_xticklabels(sorted_mice_ids,{'fontsize':10},rotation=90)
    ax[1,1].scatter(dex, hits,c=dex,cmap='plasma')
    ax[1,1].set_ylabel('Hits/session',fontsize=12)
    ax[1,1].set_xlabel('Task Weight Index',fontsize=12)
    ax[1,1].axvline(0,color='k',alpha=0.2)
    ax[1,1].set_xlim(-6,6)
    ax[1,1].set_ylim(bottom=0)

    ax[0,2].scatter(dex, false_alarms,c=dex,cmap='plasma')
    ax[0,2].set_ylabel('FA/session',fontsize=12)
    ax[0,2].set_xlabel('Task Weight Index',fontsize=12)
    ax[0,2].axvline(0,color='k',alpha=0.2)
    ax[0,2].set_xlim(-6,6)
    ax[0,2].set_ylim(bottom=0)

    ax[1,2].scatter(dex, misses,c=dex,cmap='plasma')
    ax[1,2].set_ylabel('Miss/session',fontsize=12)
    ax[1,2].set_xlabel('Task Weight Index',fontsize=12)
    ax[1,2].axvline(0,color='k',alpha=0.2)
    ax[1,2].set_xlim(-6,6)
    ax[1,2].set_ylim(bottom=0)
    plt.tight_layout()
    plt.savefig(directory+"weight_pca_4.png")

    varexpl =100*round(pca.explained_variance_ratio_[0],2)
    return dex, varexpl

def PCA_analysis(ids, mice_ids,directory):
    drop_dex,drop_varexpl = PCA_dropout(ids,mice_ids,directory)

    # PCA on weights
    weight_dex,weight_varexpl = PCA_weights(ids,mice_ids,directory)
    
    plt.figure(figsize=(5,4.5))
    scat = plt.gca().scatter(weight_dex,drop_dex,c=weight_dex, cmap='plasma')
    #plt.gca().set_xlabel('Task Weight Index \n'+str(weight_varexpl)+"% Var. Expl.",fontsize=12)
    #plt.gca().set_ylabel('Task Dropout Index \n'+str(drop_varexpl)+"% Var. Expl.",fontsize=12)
    plt.gca().set_xlabel('Task Weight Index' ,fontsize=24)
    plt.gca().set_ylabel('Task Dropout Index',fontsize=24)
    #cbar = plt.gcf().colorbar(scat, ax = plt.gca())
    #cbar.ax.set_ylabel('Task Weight Index',fontsize=20)
    plt.gca().tick_params(axis='both',labelsize=10)
    plt.xticks(fontsize=20)
    plt.yticks(fontsize=20)
    plt.tight_layout()
    plt.savefig(directory+"dropout_vs_weight_pca_1.svg")

def compare_versions(directories, IDS):
    all_rocs = []
    for d in directories:
        my_rocs = []
        for id in tqdm(IDS):
            try:
                fit = load_fit(id, directory=d)
                my_rocs.append(compute_model_roc(fit,cross_validation=True))
            except:
                pass
        all_rocs.append(my_rocs)
    return all_rocs

def compare_versions_plot(all_rocs):
    plt.figure()
    plt.ylabel('ROC')
    plt.xlabel('Model Version')
    plt.ylim(0.75,.85)
    for index, roc in enumerate(all_rocs):
        plt.plot(index, np.mean(roc),'ko')

def compare_fits(ID, directories,cv=True):
    fits = []
    roc = []
    for d in directories:
        print(d)
        fits.append(load_fit(ID,directory=d))
        roc.append(compute_model_roc(fits[-1],cross_validation=cv))
    return fits,roc
    
def compare_all_fits(IDS, directories,cv=True):
    all_fits = []
    all_roc = []
    all_ids = []
    for id in IDS:
        print(id)
        try:
            fits, roc = compare_fits(id,directories,cv=cv)
            all_fits.append(fits)
            all_roc.append(roc)
            all_ids.append(id)
        except:
            print(" crash")
    filename = directories[1] + "all_roc.pkl"
    save(filename,[all_ids,all_roc])
    return all_roc

def segment_mouse_fit(fit):
    # Takes a fit over many sessions
    # Returns a list of fit dictionaries for each session
    lengths = fit['psydata']['dayLength']
    indexes = np.cumsum(np.concatenate([[0],lengths]))
    fit['wMode_session'] = []
    fit['credibleInt_session'] = []
    fit['ypred_session'] = []
    fit['cv_pred_session'] = []
    fit['psydata_session'] = []
    for i in range(0, len(fit['psydata']['dayLength'])):
        w = fit['wMode'][:,indexes[i]:indexes[i+1]]
        fit['wMode_session'].append(w)
        w = fit['credibleInt'][:,indexes[i]:indexes[i+1]]
        fit['credibleInt_session'].append(w)
        w = fit['ypred'][indexes[i]:indexes[i+1]]
        fit['ypred_session'].append(w)
        w = fit['cv_pred'][indexes[i]:indexes[i+1]]
        fit['cv_pred_session'].append(w)
        w = fit['psydata']['y'][indexes[i]:indexes[i+1]]
        fit['psydata_session'].append(w)

def compare_roc_session_mouse(fit,directory):
    # Asking how different the ROC fits are with mouse fits
    fit['roc_session_individual'] = []
    for id in fit['good_IDS']:
        print(id)
        try:
            sfit = load_fit(id[6:],directory=directory)
            data = copy.copy(sfit['psydata']['y']-1)
            model =copy.copy(sfit['cv_pred'])
            fit['roc_session_individual'].append(roc_auc_score(data,model))
        except:
            fit['roc_session_individual'].append(np.nan)
        
def mouse_roc(fit):
    fit['roc_session'] = []
    for i in range(0,len(fit['psydata']['dayLength'])):
        data = copy.copy(fit['psydata_session'][i]-1)
        model = copy.copy(fit['cv_pred_session'][i])
        fit['roc_session'].append(roc_auc_score(data,model))

def get_all_mouse_roc(IDS,directory=None):
    labels = []
    rocs=[]
    for id in IDS:
        print(id)
        try:
            fit = load_mouse_fit(id,directory=directory)
            segment_mouse_fit(fit)
            mouse_roc(fit)
            rocs.append(fit['roc_session'])
            labels.append(fit['psydata']['session_label'])
        except:
            pass
    return labels, rocs

def compare_all_mouse_session_roc(IDS,directory=None):
    mouse_rocs = []
    session_rocs=[]
    for id in IDS:
        print(id)
        try:
            fit = load_mouse_fit(id,directory=directory)
            segment_mouse_fit(fit)
            mouse_roc(fit)
            compare_roc_session_mouse(fit,directory=directory) 
        except:
            print(" crash")
        else:
            mouse_rocs += fit['roc_session']
            session_rocs += fit['roc_session_individual']
    save(directory+"all_roc_session_mouse.pkl",[mouse_rocs,session_rocs])
    return mouse_rocs, session_rocs

def plot_all_mouse_session_roc(directory):
    rocs = load(directory+"all_roc_session_mouse.pkl")
    plt.figure()
    plt.plot(np.array(rocs[1])*100, np.array(rocs[0])*100,'ko')
    plt.plot([60,100],[60,100],'k--')
    plt.xlabel('Session ROC (%)')
    plt.ylabel('Mouse ROC (%)')
    plt.savefig(directory+"all_roc_session_mouse.png") 

def compare_mouse_roc(IDS, dir1, dir2):
    mouse_rocs1 = []
    mouse_rocs2 = []
    for id in IDS:
        print(id)
        try:
            fit1 = load_mouse_fit(id, directory=dir1)
            fit2 = load_mouse_fit(id, directory=dir2)
            segment_mouse_fit(fit1)
            segment_mouse_fit(fit2)
            mouse_roc(fit1)
            mouse_roc(fit2)
            mouse_rocs1+=fit1['roc_session']
            mouse_rocs2+=fit2['roc_session']        
        except:
            print(" crash")
    save(dir1+"all_roc_mouse_comparison.pkl",[mouse_rocs1,mouse_rocs2])
    return mouse_rocs1,mouse_rocs2

def plot_mouse_roc_comparisons(directory,label1="", label2=""):
    rocs = load(directory + "all_roc_mouse_comparison.pkl")
    plt.figure(figsize=(5.75,5))
    plt.plot(np.array(rocs[1])*100, np.array(rocs[0])*100,'ko')
    plt.plot([50,100],[50,100],'k--')
    plt.xlabel(label2+' ROC (%)')
    plt.ylabel(label1+' ROC (%)')
    plt.ylim([50,100])
    plt.xlim([50,100])
    plt.savefig(directory+"all_roc_mouse_comparison.png")


def get_session_task_index(id):
    raise Exception('outdated')
    fit = load_fit(id)
    #dropout = np.empty((len(fit['models']),))
    #for i in range(0,len(fit['models'])):
    #    dropout[i] = (1-fit['models'][i][1]/fit['models'][0][1])*100
    dropout = get_session_dropout(fit)
    model_dex = -(dropout[2] - dropout[16]) ### BUG?
    return model_dex


def hazard_index(IDS,directory,sdex = 2, edex = 6):
    dexes =[]
    for count, id in enumerate(tqdm(IDS)):
        try:
            fit = load_fit(id,directory=directory)
            #dropout = np.empty((len(fit['models']),))
            #for i in range(0,len(fit['models'])):
            #    dropout[i] = (1-fit['models'][i][1]/fit['models'][0][1])*100
            dropout = get_session_dropout(fit)
            model_dex = -(dropout[2] - dropout[6])
            session = pgt.get_data(id)
            pm.annotate_licks(session)
            bout = pt.get_bout_table(session) 
            hazard_hits, hazard_miss = pt.get_hazard(bout, None, nbins=15) 
            hazard_dex = np.sum(hazard_miss - hazard_hits)
            
            dexes.append([model_dex, hazard_dex])
        except:
            print(' crash')
    return dexes

def plot_hazard_index(dexes):
    plt.figure(figsize=(5,4))
    ax = plt.gca()
    dex = np.vstack(dexes)
    ax.scatter(dex[:,0],dex[:,1],c=-dex[:,0],cmap='plasma')
    ax.axvline(0,color='k',alpha=0.2)
    ax.axhline(0,color='k',alpha=0.2)
    ax.set_xlabel('Model Index (Task-Timing) \n <-- more timing      more task -->',fontsize=12)
    ax.set_ylabel('Hazard Function Index',fontsize=12)
    ax.set_xlim([-20, 20])
    plt.tight_layout()

def get_weight_timing_index_fit(fit):
    '''
        Return Task/Timing Index from average weights
    '''
    weights = get_weights_list(fit['weights'])
    wMode = fit['wMode']
    avg_weight_task   = np.mean(wMode[np.where(np.array(weights) == 'task0')[0][0],:])
    avg_weight_timing = np.mean(wMode[np.where(np.array(weights) == 'timing1D')[0][0],:])
    index = avg_weight_task - avg_weight_timing
    return index
    

def get_timing_index(id, directory,taskdex=2, timingdex=6,return_all=False):
    try:
        fit = load_fit(id,directory=directory)
        dropout = get_session_dropout(fit)
        model_dex = -(dropout[taskdex] - dropout[timingdex])
    except:
        model_dex = np.nan
    if return_all:
        return model_dex, dropout[taskdex], dropout[timingdex]
    else:
        return model_dex

def get_timing_index_fit(fit,taskdex=2, timingdex=6,return_all=False):
    dropout = get_session_dropout(fit)
    model_dex = -(dropout[taskdex] - dropout[timingdex])
    if return_all:
        return model_dex, dropout[taskdex], dropout[timingdex]
    else:
        return model_dex   
 
def get_session_dropout(fit):
    dropout = np.empty((len(fit['models']),))
    for i in range(0,len(fit['models'])):
        dropout[i] = (1-fit['models'][i][1]/fit['models'][0][1])*100
    return dropout
   
def get_lick_fraction(fit,first_half=False, second_half=False):
    if first_half:
        numflash = len(fit['psydata']['y'][fit['psydata']['flash_ids'] < 2400])
        numbouts = np.sum(fit['psydata']['y'][fit['psydata']['flash_ids'] < 2400] -1)
        return numbouts/numflash    
    elif second_half:
        numflash = len(fit['psydata']['y'][fit['psydata']['flash_ids'] >= 2400])
        numbouts = np.sum(fit['psydata']['y'][fit['psydata']['flash_ids'] >= 2400]-1)
        return numbouts/numflash 
    else:
        numflash = len(fit['psydata']['y'])
        numbouts = np.sum(fit['psydata']['y']-1)
        return numbouts/numflash 
 
def get_hit_fraction(fit,first_half=False, second_half=False):
    if first_half:
        numhits = np.sum(fit['psydata']['hits'][fit['psydata']['flash_ids'] < 2400])
        numbouts = np.sum(fit['psydata']['y'][fit['psydata']['flash_ids'] < 2400]-1)
        return numhits/numbouts       
    elif second_half:
        numhits = np.sum(fit['psydata']['hits'][fit['psydata']['flash_ids'] >= 2400])
        numbouts = np.sum(fit['psydata']['y'][fit['psydata']['flash_ids'] >= 2400]-1)
        return numhits/numbouts    
    else:
        numhits = np.sum(fit['psydata']['hits'])
        numbouts = np.sum(fit['psydata']['y']-1)
        return numhits/numbouts    

def get_trial_hit_fraction(fit,first_half=False, second_half=False):
    if first_half:
        numhits = np.sum(fit['psydata']['hits'][fit['psydata']['flash_ids'] < 2400])
        nummiss = np.sum(fit['psydata']['misses'][fit['psydata']['flash_ids'] < 2400])
        return numhits/(numhits+nummiss)   
    elif second_half:
        numhits = np.sum(fit['psydata']['hits'][fit['psydata']['flash_ids'] >= 2400])
        nummiss = np.sum(fit['psydata']['misses'][fit['psydata']['flash_ids'] >= 2400])
        return numhits/(numhits+nummiss)
    else:
        numhits = np.sum(fit['psydata']['hits'])
        nummiss = np.sum(fit['psydata']['misses'])
        return numhits/(numhits+nummiss)

def get_all_timing_index(ids, directory,hit_threshold=50):
    df = pd.DataFrame(data={'Task/Timing Index':[],'taskdex':[],'timingdex':[],'numlicks':[],'behavior_session_id':[]})
    crashed = 0
    low_hits = 0
    for id in ids:
        try:
            fit = load_fit(id, directory=directory)
            if np.sum(fit['psydata']['hits']) > hit_threshold:
                model_dex, taskdex,timingdex = get_timing_index_fit(fit,return_all=True)
                numlicks = np.sum(fit['psydata']['y']-1) 
                d = {'Task/Timing Index':model_dex,'taskdex':taskdex,'timingdex':timingdex,'numlicks':numlicks,'behavior_session_id':id}
                df = df.append(d,ignore_index=True)
            else:
                low_hits +=1
        except:
            crashed+=1
    print(str(crashed) + " crashed")
    print(str(low_hits) + " below hit_threshold")
    return df.set_index('behavior_session_id')

def plot_model_index_summaries(df,directory):

    fig, ax = plt.subplots(figsize=(6,4.5))
    scat = ax.scatter(-df.taskdex, -df.timingdex,c=df['Task/Timing Index'],cmap='plasma')
    ax.set_ylabel('Timing Dropout',fontsize=24)
    ax.set_xlabel('Task Dropout',fontsize=24)
    plt.xticks(fontsize=20)
    plt.yticks(fontsize=20)
    cbar = fig.colorbar(scat, ax = ax)
    cbar.ax.set_ylabel('Task Dropout Index',fontsize=20)
    plt.tight_layout()
    plt.savefig(directory+'timing_vs_task_breakdown_1.svg')




    fig, ax = plt.subplots(nrows=2,ncols=2,figsize=(8,5))
    scat = ax[0,0].scatter(-df.taskdex, -df.timingdex,c=df['Task/Timing Index'],cmap='plasma')
    ax[0,0].set_ylabel('Timing Dex')
    ax[0,0].set_xlabel('Task Dex')
    cbar = fig.colorbar(scat, ax = ax[0,0])
    cbar.ax.set_ylabel('Dropout % \n (Task - Timing)',fontsize=12)

    scat = ax[0,1].scatter(df['Task/Timing Index'], df['numlicks'],c=df['Task/Timing Index'],cmap='plasma')
    ax[0,1].set_xlabel('Task/Timing Index')
    ax[0,1].set_ylabel('Number Lick Bouts')
    cbar = fig.colorbar(scat, ax = ax[0,1])
    cbar.ax.set_ylabel('Dropout % \n (Task - Timing)',fontsize=12)
    
    scat = ax[1,0].scatter(-df['taskdex'],df['numlicks'],c=df['Task/Timing Index'],cmap='plasma')
    ax[1,0].set_xlabel('Task Dex')
    ax[1,0].set_ylabel('Number Lick Bouts')
    cbar = fig.colorbar(scat, ax = ax[1,0])
    cbar.ax.set_ylabel('Dropout % \n (Task - Timing)',fontsize=12)

    scat = ax[1,1].scatter(-df['timingdex'],df['numlicks'],c=df['Task/Timing Index'],cmap='plasma')
    ax[1,1].set_xlabel('Timing Dex')
    ax[1,1].set_ylabel('Number Lick Bouts')
    cbar = fig.colorbar(scat, ax = ax[1,1])
    cbar.ax.set_ylabel('Dropout % \n (Task - Timing)',fontsize=12)
    plt.tight_layout()
    plt.savefig(directory+'timing_vs_task_breakdown.png')

def compute_model_roc_timing(fit,plot_this=False):
    '''
        Computes area under the ROC curve for the model in fit. If plot_this, then plots the ROC curve. 
        If cross_validation, then uses the cross validated prediction in fit, not he training fit.
        Returns the AU. ROC single float
    '''

    data = copy.copy(fit['psydata']['y']-1)
    model       = copy.copy(fit['cv_pred'])
    pre_model   = copy.copy(fit['preliminary']['cv_pred'])
    s_model     = copy.copy(fit['session_timing']['cv_pred'])

    if plot_this:
        plt.figure()
        alarms,hits,thresholds = roc_curve(data,model)
        pre_alarms,pre_hits,pre_thresholds = roc_curve(data,pre_model)
        s_alarms,s_hits,s_thresholds = roc_curve(data,s_model)
        plt.plot(alarms,hits,'r-',label='Average')
        plt.plot(pre_alarms,pre_hits,'k-',label='10 Regressors')
        plt.plot(s_alarms,s_hits,'b-',label='Session 1D')
        plt.plot([0,1],[0,1],'k--')
        plt.ylabel('Hits')
        plt.xlabel('False Alarms')
        plt.legend()
    return roc_auc_score(data,model), roc_auc_score(data,pre_model), roc_auc_score(data,s_model)

def compare_timing_versions(ids, directory):
    rocs = []
    for id in ids:
        try:
            fit = load_fit(id,directory=directory)
            roc = compute_model_roc_timing(fit)
            rocs.append(roc)
        except:
            pass
    rocs = np.vstack(rocs)
    
    plt.figure()
    plt.plot(rocs.T,'o')
    means =np.mean(rocs,0)
    for i in range(0,3):
        plt.plot([i-0.25,i+0.25],[means[i], means[i]],'k-',linewidth=2)
    plt.ylim([0.5, 1])
    plt.gca().set_xticks([0, 1,2])
    plt.gca().set_xticklabels(['1D Average','10 Timing','1D Session'],{'fontsize':12})
    plt.ylabel('CV ROC')
    
    plt.figure()
    plt.plot(rocs[:,0],rocs[:,1],'o')
    plt.plot([0.5,1],[0.5,1],'k--',alpha=0.3)
    plt.ylabel('CV ROC - Session specific')
    plt.xlabel('CV ROC - Average Timing')
    
    return rocs


def summarize_fits(ids, directory):
    crashed = 0
    for id in tqdm(ids):
        try:
            fit = load_fit(id, directory=directory)
            summarize_fit(fit,directory=directory, savefig=True)
        except Exception as e:
            print(e)
            crashed +=1
        plt.close('all')
    print(str(crashed) + " crashed")

def build_manifest_by_task_index():
    raise Exception('outdated')
    manifest = get_manifest().query('active').copy()
    manifest['task_index'] = manifest.apply(lambda x: get_timing_index(x['ophys_experiment_id'],dir),axis=1)
    task_vals = manifest['task_index']
    mean_index = np.mean(task_vals[~np.isnan(task_vals)])
    manifest['task_session'] = manifest.apply(lambda x: x['task_index'] > mean_index,axis=1)
    return  manifest.groupby(['cre_line','imaging_depth','container_id']).apply(lambda x: np.sum(x['task_session']) >=2)

def build_model_training_manifest(directory=None,verbose=False, use_full_ophys=True,full_container=True,hit_threshold=10):
    '''
        Builds a manifest of model results
        Each row is a behavior_session_id
        
        if verbose, logs each crashed session id
        if use_full_ophys, uses the full model for ophys sessions (includes omissions)
    
    '''
    manifest = pgt.get_training_manifest().query('active').copy()
    
    if type(directory) == type(None):
        directory=global_directory     

    manifest['good'] = manifest['active'] #Just copying the column size
    first = True
    crashed = 0
    for index, row in manifest.iterrows():
        try:
            ophys= (row.ophys) & (row.stage > "0") & use_full_ophys
            fit = load_fit(row.name, directory=directory, TRAIN= not ophys)
        except:
            if verbose:
                print(str(row.name)+" crash")
            manifest.at[index,'good'] = False
            crashed +=1
        else:
            manifest.at[index,'good'] = True
            manifest.at[index, 'num_hits'] = np.sum(fit['psydata']['hits'])
            manifest.at[index, 'num_fa'] = np.sum(fit['psydata']['false_alarms'])
            manifest.at[index, 'num_cr'] = np.sum(fit['psydata']['correct_reject'])
            manifest.at[index, 'num_miss'] = np.sum(fit['psydata']['misses'])
            manifest.at[index, 'num_aborts'] = np.sum(fit['psydata']['aborts'])
            sigma = fit['hyp']['sigma']
            wMode = fit['wMode']
            weights = get_weights_list(fit['weights'])
            manifest.at[index,'session_roc'] = compute_model_roc(fit)
            manifest.at[index,'lick_fraction'] = get_lick_fraction(fit)
            manifest.at[index,'lick_fraction_1st'] = get_lick_fraction(fit,first_half=True)
            manifest.at[index,'lick_fraction_2nd'] = get_lick_fraction(fit,second_half=True)
            manifest.at[index,'lick_hit_fraction'] = get_hit_fraction(fit)
            manifest.at[index,'lick_hit_fraction_1st'] = get_hit_fraction(fit,first_half=True)
            manifest.at[index,'lick_hit_fraction_2nd'] = get_hit_fraction(fit,second_half=True)
            manifest.at[index,'trial_hit_fraction'] = get_trial_hit_fraction(fit)
            manifest.at[index,'trial_hit_fraction_1st'] = get_trial_hit_fraction(fit,first_half=True)
            manifest.at[index,'trial_hit_fraction_2nd'] = get_trial_hit_fraction(fit,second_half=True)
   
            if ophys:
                timing_index = 6
            else:
                timing_index = 3
            model_dex, taskdex,timingdex = get_timing_index_fit(fit,timingdex = timing_index,return_all=True)
            manifest.at[index,'task_dropout_index'] = model_dex
            manifest.at[index,'task_only_dropout_index'] = taskdex
            manifest.at[index,'timing_only_dropout_index'] = timingdex
 
            for dex, weight in enumerate(weights):
                manifest.at[index, 'prior_'+weight] =sigma[dex]
                manifest.at[index, 'avg_weight_'+weight] = np.mean(wMode[dex,:])
                manifest.at[index, 'avg_weight_'+weight+'_1st'] = np.mean(wMode[dex,fit['psydata']['flash_ids']<2400])
                manifest.at[index, 'avg_weight_'+weight+'_2nd'] = np.mean(wMode[dex,fit['psydata']['flash_ids']>=2400])
                if first: 
                    manifest['weight_'+weight] = [[]]*len(manifest)
                manifest.at[index, 'weight_'+str(weight)] = wMode[dex,:]  
            first = False
    print(str(crashed)+ " sessions crashed")

    manifest = manifest.query('good').copy()
    manifest['task_weight_index'] = manifest['avg_weight_task0'] - manifest['avg_weight_timing1D']
    manifest['task_weight_index_1st'] = manifest['avg_weight_task0_1st'] - manifest['avg_weight_timing1D_1st']
    manifest['task_weight_index_2nd'] = manifest['avg_weight_task0_2nd'] - manifest['avg_weight_timing1D_2nd']
    manifest['task_session'] = -manifest['task_only_dropout_index'] > -manifest['timing_only_dropout_index']



    manifest['full_container'] = manifest.apply(lambda x: len(manifest.query('ophys & (stage > "0")'))>=4,axis=1)
    if full_container:
        n_remove = len(manifest.query('not full_container'))
        print(str(n_remove) + " sessions from incomplete containers")
        manifest = manifest.query('full_container')

    n_remove = len(manifest.query('num_hits < @hit_threshold'))
    print(str(n_remove) + " sessions with low hits")
    manifest = manifest.query('num_hits >= @hit_threshold')

    n = len(manifest)
    print(str(n) + " sessions returned")
    
    return manifest

def build_model_manifest(directory=None,container_in_order=False, full_container=False,verbose=False,include_hit_threshold=True,hit_threshold=10):
    '''
        Builds a manifest of model results
        Each row is a Behavior_session_id
        
        if container_in_order, then only returns sessions that come from a container that was collected in order. The container
            does not need to be complete, as long as the sessions that are present were collected in order
        if full_container, then only returns sessions that come from a container with 4 active sessions. 
        if verbose, logs each crashed session id
    
    '''
    manifest = pgt.get_manifest().query('active').copy()
    
    if type(directory) == type(None):
        directory=global_directory     

    manifest['good'] = manifest['trained_A'] #Just copying the column size
    first = True
    crashed = 0
    for index, row in manifest.iterrows():
        try:
            fit = load_fit(row.name,directory=directory)
        except:
            if verbose:
                print(str(row.name)+" crash")
            manifest.at[index,'good'] = False
            crashed +=1
        else:
            manifest.at[index,'good'] = True
            manifest.at[index, 'num_hits'] = np.sum(fit['psydata']['hits'])
            manifest.at[index, 'num_fa'] = np.sum(fit['psydata']['false_alarms'])
            manifest.at[index, 'num_cr'] = np.sum(fit['psydata']['correct_reject'])
            manifest.at[index, 'num_miss'] = np.sum(fit['psydata']['misses'])
            manifest.at[index, 'num_aborts'] = np.sum(fit['psydata']['aborts'])
            sigma = fit['hyp']['sigma']
            wMode = fit['wMode']
            weights = get_weights_list(fit['weights'])
            manifest.at[index,'session_roc'] = compute_model_roc(fit)
            manifest.at[index,'lick_fraction'] = get_lick_fraction(fit)
            manifest.at[index,'lick_fraction_1st'] = get_lick_fraction(fit,first_half=True)
            manifest.at[index,'lick_fraction_2nd'] = get_lick_fraction(fit,second_half=True)
            manifest.at[index,'lick_hit_fraction'] = get_hit_fraction(fit)
            manifest.at[index,'lick_hit_fraction_1st'] = get_hit_fraction(fit,first_half=True)
            manifest.at[index,'lick_hit_fraction_2nd'] = get_hit_fraction(fit,second_half=True)
            manifest.at[index,'trial_hit_fraction'] = get_trial_hit_fraction(fit)
            manifest.at[index,'trial_hit_fraction_1st'] = get_trial_hit_fraction(fit,first_half=True)
            manifest.at[index,'trial_hit_fraction_2nd'] = get_trial_hit_fraction(fit,second_half=True)
   
            model_dex, taskdex,timingdex = get_timing_index_fit(fit,return_all=True)
            manifest.at[index,'task_dropout_index'] = model_dex
            manifest.at[index,'task_only_dropout_index'] = taskdex
            manifest.at[index,'timing_only_dropout_index'] = timingdex
 
            for dex, weight in enumerate(weights):
                manifest.at[index, 'prior_'+weight] =sigma[dex]
                manifest.at[index, 'avg_weight_'+weight] = np.mean(wMode[dex,:])
                manifest.at[index, 'avg_weight_'+weight+'_1st'] = np.mean(wMode[dex,fit['psydata']['flash_ids']<2400])
                manifest.at[index, 'avg_weight_'+weight+'_2nd'] = np.mean(wMode[dex,fit['psydata']['flash_ids']>=2400])
                if first: 
                    manifest['weight_'+weight] = [[]]*len(manifest)
                manifest.at[index, 'weight_'+str(weight)] = wMode[dex,:]  
            first = False
    print(str(crashed)+ " sessions crashed")

    manifest = manifest.query('good').copy()
    manifest['task_weight_index'] = manifest['avg_weight_task0'] - manifest['avg_weight_timing1D']
    manifest['task_weight_index_1st'] = manifest['avg_weight_task0_1st'] - manifest['avg_weight_timing1D_1st']
    manifest['task_weight_index_2nd'] = manifest['avg_weight_task0_2nd'] - manifest['avg_weight_timing1D_2nd']
    manifest['task_session'] = -manifest['task_only_dropout_index'] > -manifest['timing_only_dropout_index']

    in_order = []
    for index, mouse in enumerate(manifest['container_id'].unique()):
        this_df = manifest.query('container_id == @mouse')
        s1 = this_df.query('session_type == "OPHYS_1_images_A"')['date_of_acquisition'].values
        s3 = this_df.query('session_type == "OPHYS_3_images_A"')['date_of_acquisition'].values
        s4 = this_df.query('session_type == "OPHYS_4_images_B"')['date_of_acquisition'].values
        s6 = this_df.query('session_type == "OPHYS_6_images_B"')['date_of_acquisition'].values
        stages = np.concatenate([s1,s3,s4,s6])
        if np.all(stages ==sorted(stages)):
            in_order.append(mouse)
    manifest['container_in_order'] = manifest.apply(lambda x: x['container_id'] in in_order, axis=1)
    manifest['full_container'] = manifest.apply(lambda x: len(manifest.query('container_id == @x.container_id'))==4,axis=1)

    if container_in_order:
        n_remove = len(manifest.query('not container_in_order'))
        print(str(n_remove) + " sessions out of order")
        manifest = manifest.query('container_in_order')
    if full_container:
        n_remove = len(manifest.query('not full_container'))
        print(str(n_remove) + " sessions from incomplete containers")
        manifest = manifest.query('full_container')
        if not (np.mod(len(manifest),4) == 0):
            raise Exception('Filtered for full containers, but dont seem to have the right number')
    if include_hit_threshold:
        n_remove = len(manifest.query('num_hits < @hit_threshold'))
        print(str(n_remove) + " sessions with low hits")
        manifest = manifest.query('num_hits >=@hit_threshold')
    n = len(manifest)
    print(str(n) + " sessions returned")
    
    return manifest

def plot_all_manifest_by_stage(manifest, directory,savefig=True, group_label='all'):
    plot_manifest_by_stage(manifest,'session_roc',hline=0.5,ylims=[0.5,1],directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'lick_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'lick_hit_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'trial_hit_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'task_dropout_index',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'task_weight_index',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'prior_bias',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'prior_task0',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'prior_omissions1',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'prior_timing1D',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_bias',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_task0',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_omissions1',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_timing1D',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_task0_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_task0_2nd',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_timing1D_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_timing1D_2nd',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_bias_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_stage(manifest,'avg_weight_bias_2nd',directory=directory,savefig=savefig,group_label=group_label)

def plot_all_manifest_by_cre(manifest, directory,savefig=True, group_label='all'):
    plot_manifest_by_cre(manifest,'session_roc',hline=0.5,ylims=[0.5,1],directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'lick_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'lick_hit_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'trial_hit_fraction',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'task_dropout_index',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'task_weight_index',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'prior_bias',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'prior_task0',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'prior_omissions1',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'prior_timing1D',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_bias',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_task0',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_omissions1',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_timing1D',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_task0_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_task0_2nd',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_timing1D_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_timing1D_2nd',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_bias_1st',directory=directory,savefig=savefig,group_label=group_label)
    plot_manifest_by_cre(manifest,'avg_weight_bias_2nd',directory=directory,savefig=savefig,group_label=group_label)



def compare_all_manifest_by_stage(manifest, directory, savefig=True, group_label='all'):
    compare_manifest_by_stage(manifest,['3','4'], 'task_weight_index',directory=directory,savefig=savefig,group_label=group_label)
    compare_manifest_by_stage(manifest,['3','4'], 'task_dropout_index',directory=directory,savefig=savefig,group_label=group_label)    
    compare_manifest_by_stage(manifest,['3','4'], 'avg_weight_task0',directory=directory,savefig=savefig,group_label=group_label)
    compare_manifest_by_stage(manifest,['3','4'], 'avg_weight_timing1D',directory=directory,savefig=savefig,group_label=group_label)
    compare_manifest_by_stage(manifest,['3','4'], 'session_roc',directory=directory,savefig=savefig,group_label=group_label)

def plot_manifest_by_stage(manifest, key,ylims=None,hline=0,directory=None,savefig=True,group_label='all',stage_names=None,fs1=12,fs2=12,filetype='.png',force_fig_size=None):
    means = manifest.groupby('stage')[key].mean()
    sem = manifest.groupby('stage')[key].sem()
    if type(force_fig_size) == type(None):
        plt.figure()
    else:
        plt.figure(figsize=force_fig_size)
    colors = sns.color_palette("hls",len(means))
    for index, m in enumerate(means):
        plt.plot([index-0.5,index+0.5], [m, m],'-',color=colors[index],linewidth=4)
        plt.plot([index, index],[m-sem[index], m+sem[index]],'-',color=colors[index])
    if type(stage_names) == type(None):
        stage_names = np.array(manifest.groupby('stage')[key].mean().index) 
    plt.gca().set_xticks(np.arange(0,len(stage_names)))
    plt.gca().set_xticklabels(stage_names,rotation=0,fontsize=fs1)
    plt.gca().axhline(hline, alpha=0.3,color='k',linestyle='--')
    plt.yticks(fontsize=fs2)
    plt.ylabel(key,fontsize=fs1)
    stage3, stage4 = get_manifest_values_by_stage(manifest, ['3','4'],key)
    pval =  ttest_rel(stage3,stage4,nan_policy='omit')
    ylim = plt.ylim()[1]
    plt.plot([1,2],[ylim*1.05, ylim*1.05],'k-')
    plt.plot([1,1],[ylim, ylim*1.05], 'k-')
    plt.plot([2,2],[ylim, ylim*1.05], 'k-')

    if pval[1] < 0.05:
        plt.plot(1.5, ylim*1.1,'k*')
    else:
        plt.text(1.5,ylim*1.1, 'ns')
    if not (type(ylims) == type(None)):
        plt.ylim(ylims)
    plt.tight_layout()    

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_stage_comparisons_"+key+filetype)

def get_manifest_values_by_cre(manifest,key):
    x = manifest.cre_line.unique()[0] 
    y = manifest.cre_line.unique()[1]
    z = manifest.cre_line.unique()[2]
    s1df = manifest.query('cre_line ==@x')[key].drop_duplicates(keep='last')
    s2df = manifest.query('cre_line ==@y')[key].drop_duplicates(keep='last')
    s3df = manifest.query('cre_line ==@z')[key].drop_duplicates(keep='last')
    return s1df.values, s2df.values, s3df.values 

def get_manifest_values_by_stage(manifest, stages, key):
    x = stages[0]
    y = stages[1]
    s1df = manifest.set_index(['container_id']).query('stage ==@x')[key].drop_duplicates(keep='last')
    s2df = manifest.set_index(['container_id']).query('stage ==@y')[key].drop_duplicates(keep='last')
    s1df.name=x
    s2df.name=y
    full_df = s1df.to_frame().join(s2df)
    vals1 = full_df[x].values 
    vals2 = full_df[y].values 
    return vals1,vals2  

def compare_manifest_by_stage(manifest,stages, key,directory=None,savefig=True,group_label='all'):
    '''
        Function for plotting various metrics by ophys_stage
        compare_manifest_by_stage(manifest,['1','3'],'avg_weight_task0')
    '''
    # Get the stage values paired by container
    vals1, vals2 = get_manifest_values_by_stage(manifest, stages, key)

    plt.figure(figsize=(6,5))
    plt.plot(vals1,vals2,'ko')
    xlims = plt.xlim()
    ylims = plt.ylim()
    all_lims = np.concatenate([xlims,ylims])
    lims = [np.min(all_lims), np.max(all_lims)]
    plt.plot(lims,lims, 'k--')
    plt.xlabel(stages[0],fontsize=12)
    plt.ylabel(stages[1],fontsize=12)
    plt.title(key)
    pval = ttest_rel(vals1,vals2,nan_policy='omit')
    ylim = plt.ylim()[1]
    if pval[1] < 0.05:
        plt.title(key+": *")
    else:
        plt.title(key+": ns")
    plt.tight_layout()    

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_stage_comparisons_"+stages[0]+"_"+stages[1]+"_"+key+".png")

def plot_static_comparison(IDS, directory=None,savefig=False,group_label=""):
    '''
        Top Level function for comparing static and dynamic logistic regression using ROC scores
    '''

    if type(directory) == type(None):
        directory = global_directory

    all_s, all_d = get_all_static_comparisons(IDS, directory)
    plot_static_comparison_inner(all_s,all_d,directory=directory, savefig=savefig, group_label=group_label)

def plot_static_comparison_inner(all_s,all_d,directory=None, savefig=False,group_label="",fs1=12,fs2=12,filetype='.png'): 
    '''
        Plots static and dynamic ROC comparisons
    
    '''
    fig,ax = plt.subplots(figsize=(5,4))
    plt.plot(all_s,all_d,'ko')
    plt.plot([0.5,1],[0.5,1],'k--')
    plt.ylabel('Dynamic ROC',fontsize=fs1)
    plt.xlabel('Static ROC',fontsize=fs1)
    plt.xticks(fontsize=fs2)
    plt.yticks(fontsize=fs2)
    plt.tight_layout()
    if savefig:
        plt.savefig(directory+"summary_static_comparison"+group_label+filetype)

def get_all_static_comparisons(IDS, directory):
    '''
        Iterates through list of session ids and gets static and dynamic ROC scores
    '''
    all_s = []
    all_d = []    

    for index, id in enumerate(IDS):
        try:
            fit = load_fit(id, directory=directory)
            static,dynamic = get_static_roc(fit)
        except:
            pass
        else:
            all_s.append(static)
            all_d.append(dynamic)

    return all_s, all_d

def get_static_design_matrix(fit):
    '''
        Returns the design matrix to be used for static logistic regression, does not include bias
    '''
    X = []
    for index, w in enumerate(fit['weights'].keys()):
        if fit['weights'][w]:
            if not (w=='bias'):
                X.append(fit['psydata']['inputs'][w]) 
    return np.hstack(X)

def get_static_roc(fit,use_cv=False):
    '''
        Returns the area under the ROC curve for a static logistic regression model
    '''
    X = get_static_design_matrix(fit)
    y = fit['psydata']['y'] - 1
    if use_cv:
        clf = logregcv(cv=10)
    else:
        clf = logreg(penalty='none',solver='lbfgs')
    clf.fit(X,y)
    ypred = clf.predict(X)
    fpr, tpr, thresholds = metrics.roc_curve(y,ypred)
    static_roc = metrics.auc(fpr,tpr)
    dfpr, dtpr, dthresholds = metrics.roc_curve(y,fit['cv_pred'])
    dynamic_roc = metrics.auc(dfpr,dtpr)   
    return static_roc, dynamic_roc

def plot_manifest_by_cre(manifest,key,ylims=None,hline=0,directory=None,savefig=True,group_label='all',fs1=12,fs2=12,rotation=0,labels=None,figsize=None,ylabel=None):
    means = manifest.groupby('cre_line')[key].mean()
    sem  = manifest.groupby('cre_line')[key].sem()
    if figsize is None:
        plt.figure()
    else:
        plt.figure(figsize=figsize)
    colors = sns.color_palette("hls",len(means))
    for index, m in enumerate(means):
        plt.plot([index-0.5,index+0.5], [m, m],'-',color=colors[index],linewidth=4)
        plt.plot([index, index],[m-sem[index], m+sem[index]],'-',color=colors[index])
    if labels is None:
        names = np.array(manifest.groupby('cre_line')[key].mean().index) 
    else:
        names = labels
    plt.gca().set_xticks(np.arange(0,len(names)))
    plt.gca().set_xticklabels(names,rotation=rotation,fontsize=fs1)
    plt.gca().axhline(hline, alpha=0.3,color='k',linestyle='--')
    plt.yticks(fontsize=fs2)
    if ylabel is None:
        plt.ylabel(key,fontsize=fs1)
    else:
        plt.ylabel(ylabel,fontsize=fs1)
    c1,c2,c3 = get_manifest_values_by_cre(manifest,key)
    pval12 =  ttest_ind(c1,c2,nan_policy='omit')
    pval13 =  ttest_ind(c1,c3,nan_policy='omit')
    pval23 =  ttest_ind(c2,c3,nan_policy='omit')
    ylim = plt.ylim()[1]
    r = plt.ylim()[1] - plt.ylim()[0]
    sf = .075
    offset = 2 
    plt.plot([0,1],[ylim+r*sf, ylim+r*sf],'k-')
    plt.plot([0,0],[ylim, ylim+r*sf], 'k-')
    plt.plot([1,1],[ylim, ylim+r*sf], 'k-')
 
    plt.plot([0,2],[ylim+r*sf*3, ylim+r*sf*3],'k-')
    plt.plot([0,0],[ylim+r*sf*2, ylim+r*sf*3], 'k-')
    plt.plot([2,2],[ylim+r*sf*2, ylim+r*sf*3], 'k-')

    plt.plot([1,2],[ylim+r*sf, ylim+r*sf],'k-')
    plt.plot([1,1],[ylim, ylim+r*sf], 'k-')
    plt.plot([2,2],[ylim, ylim+r*sf], 'k-')

    if pval12[1] < 0.05:
        plt.plot(.5, ylim+r*sf*1.5,'k*')
    else:
        plt.text(.5,ylim+r*sf*1.25, 'ns')

    if pval13[1] < 0.05:
        plt.plot(1, ylim+r*sf*3.5,'k*')
    else:
        plt.text(1,ylim+r*sf*3.5, 'ns')

    if pval23[1] < 0.05:
        plt.plot(1.5, ylim+r*sf*1.5,'k*')
    else:
        plt.text(1.5,ylim+r*sf*1.25, 'ns')

    if not (type(ylims) == type(None)):
        plt.ylim(ylims)
    plt.tight_layout()    

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_cre_comparisons_"+key+".png")
        plt.savefig(directory+group_label+"_cre_comparisons_"+key+".svg")

def plot_task_index_by_cre(manifest,directory=None,savefig=True,group_label='all'):
    plt.figure(figsize=(5,4))
    cre = manifest.cre_line.unique()
    colors = sns.color_palette("hls",len(cre))
    for i in range(0,len(cre)):
        x = manifest.cre_line.unique()[i]
        df = manifest.query('cre_line == @x')
        plt.plot(-df['task_only_dropout_index'], -df['timing_only_dropout_index'], 'o',color=colors[i],label=x)
    plt.plot([0,40],[0,40],'k--',alpha=0.5)
    plt.ylabel('Timing Dropout',fontsize=20)
    plt.xlabel('Task Dropout',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.legend()
    plt.tight_layout()

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_cre.png")
        plt.savefig(directory+group_label+"_task_index_by_cre.svg")

    plt.figure(figsize=(8,3))
    cre = manifest.cre_line.unique()
    colors = sns.color_palette("hls",len(cre))
    s = 0
    for i in range(0,len(cre)):
        x = manifest.cre_line.unique()[i]
        df = manifest.query('cre_line == @x')
        plt.plot(np.arange(s,s+len(df)), df['task_dropout_index'].sort_values(), 'o',color=colors[i],label=x)
        s += len(df)
    plt.axhline(0,ls='--',color='k',alpha=0.5)
    plt.ylabel('Task/Timing Dropout Index',fontsize=12)
    plt.xlabel('Session',fontsize=12)
    plt.legend()
    plt.tight_layout()

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_cre_each_sequence.png")
        plt.savefig(directory+group_label+"_task_index_by_cre_each_sequence.svg")

    plt.figure(figsize=(8,3))
    cre = manifest.cre_line.unique()
    colors = sns.color_palette("hls",len(cre))
    sorted_manifest = manifest.sort_values(by='task_dropout_index')
    count = 0
    for index, row in sorted_manifest.iterrows():
        if row.cre_line == cre[0]:
            plt.plot(count, row.task_dropout_index, 'o',color=colors[0])
        elif row.cre_line == cre[1]:
            plt.plot(count,row.task_dropout_index, 'o',color=colors[1])
        else:
            plt.plot(count,row.task_dropout_index, 'o',color=colors[2])
        count+=1
    plt.axhline(0,ls='--',color='k',alpha=0.5)
    plt.ylabel('Task/Timing Dropout Index',fontsize=12)
    plt.xlabel('Session',fontsize=12)
    plt.tight_layout()

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_cre_sequence.png")
        plt.savefig(directory+group_label+"_task_index_by_cre_sequence.svg")

    plt.figure(figsize=(5,4))
    counts,edges = np.histogram(manifest['task_dropout_index'].values,20)
    plt.axvline(0,ls='--',color='k',alpha=0.5)
    for i in range(0,len(cre)):
        x = manifest.cre_line.unique()[i]
        df = manifest.query('cre_line == @x')
        plt.hist(df['task_dropout_index'].values, bins=edges,alpha=0.5,color=colors[i],label=x)
    plt.ylabel('Count',fontsize=20)
    plt.xlabel('Task/Timing Dropout Index',fontsize=20)
    plt.xticks(fontsize=16)
    plt.yticks(fontsize=16)
    plt.legend()
    plt.tight_layout()

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_cre_histogram.png")
        plt.savefig(directory+group_label+"_task_index_by_cre_histogram.svg")

def plot_manifest_by_date(manifest,directory=None,savefig=True,group_label='all',plot_by=4):
    manifest = manifest.sort_values(by=['date_of_acquisition'])
    plt.figure(figsize=(8,4))
    #cre = manifest.cre_line.unique()
    #colors = sns.color_palette("hls",len(cre))
    #for i in range(0,len(cre)):
    #    x = manifest.cre_line.unique()[i]
    #    df = manifest.query('cre_line == @x')
    #    plt.plot(df.date_of_acquisition,df.task_dropout_index,'o',color=colors[i])
    plt.plot(manifest.date_of_acquisition,manifest.task_dropout_index,'ko')
    plt.axhline(0,ls='--',color='k',alpha=0.5)
    plt.gca().set_xticks(manifest.date_of_acquisition.values[::plot_by])
    labels = manifest.date_of_acquisition.values[::plot_by]
    labels = [x[0:10] for x in labels]
    plt.gca().set_xticklabels(labels,rotation=-90)
    plt.ylabel('Task/Timing Dropout Index',fontsize=12)
    plt.xlabel('Date of Acquisition',fontsize=12)
    plt.tight_layout()

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_date.png")

def plot_task_timing_over_session(manifest,directory=None,savefig=True,group_label='all'):
    weight_task_index_by_flash = [manifest.loc[x]['weight_task0'] - manifest.loc[x]['weight_timing1D'] for x in manifest.index]
    wtibf = np.vstack([x[0:3200] for x in weight_task_index_by_flash])
    plt.figure(figsize=(8,3))
    for x in weight_task_index_by_flash:
        plt.plot(x,'k',alpha=0.1)
    plt.plot(np.mean(wtibf,0),linewidth=4)
    plt.axhline(0,ls='--',color='k')
    plt.ylim(-5,5)
    plt.xlim(0,3200)
    plt.ylabel('Task/Timing Dropout Index',fontsize=12)
    plt.xlabel('Flash # in session',fontsize=12)
    plt.tight_layout()

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_task_index_over_session.png")


def plot_task_timing_by_training_duration(model_manifest,directory=None, savefig=True,group_label='all'):
    avg_index = []
    num_train_sess = []
    cache = pgt.get_cache()
    behavior_sessions = cache.get_behavior_session_table()
    ophys_list = [  'OPHYS_1_images_A', 'OPHYS_3_images_A', 'OPHYS_4_images_B', 'OPHYS_5_images_B_passive',
                'OPHYS_6_images_B', 'OPHYS_2_images_A_passive', 'OPHYS_1_images_B',
                'OPHYS_2_images_B_passive', 'OPHYS_3_images_B', 'OPHYS_4_images_A', 
                'OPHYS_5_images_A_passive', 'OPHYS_6_images_A']

    for index, mouse in enumerate(pgt.get_mice_ids()):
        df = behavior_sessions.query('donor_id ==@mouse')
        df['ophys'] = df['session_type'].isin(ophys_list)
        num_train_sess.append(len(df.query('not ophys')))
        avg_index.append(model_manifest.query('donor_id==@mouse').task_dropout_index.mean())

    plt.figure()
    plt.plot(avg_index, num_train_sess,'ko')
    plt.ylabel('Number of Training Sessions')
    plt.xlabel('Task/Timing Index')
    plt.axvline(0,ls='--',color='k')
    plt.axhline(0,ls='--',color='k')

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_task_index_by_train_duration.png")

def scatter_manifest(model_manifest, key1, key2, directory=None,sflip1=False,sflip2=False,cindex=None, savefig=True,group_label='all'):
    vals1 = model_manifest[key1].values
    vals2 = model_manifest[key2].values
    if sflip1:
        vals1 = -vals1
    if sflip2:
        vals2 = -vals2
    plt.figure()
    if (type(cindex) == type(None)):
       plt.plot(vals1,vals2,'ko')
    else:
        ax = plt.gca()
        scat = ax.scatter(vals1,vals2,c=model_manifest[cindex],cmap='plasma')
        cbar = plt.gcf().colorbar(scat, ax = ax)
        cbar.ax.set_ylabel(cindex,fontsize=12)
    plt.xlabel(key1)
    plt.ylabel(key2)

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        if (type(cindex) == type(None)):
            plt.savefig(directory+group_label+"_manifest_scatter_"+key1+"_by_"+key2+".png")
        else:
            plt.savefig(directory+group_label+"_manifest_scatter_"+key1+"_by_"+key2+"_with_"+cindex+"_colorbar.png")

def plot_manifest_groupby(manifest, key, group, savefig=True, directory=None, group_label='all'):
    means = manifest.groupby(group)[key].mean()
    sem = manifest.groupby(group)[key].sem()
    names = np.array(manifest.groupby(group)[key].mean().index) 
    plt.figure()
    colors = sns.color_palette("hls",len(means))
    for index, m in enumerate(means):
        plt.plot([index-0.5,index+0.5], [m, m],'-',color=colors[index],linewidth=4)
        plt.plot([index, index],[m-sem[index], m+sem[index]],'-',color=colors[index])

    plt.gca().set_xticks(np.arange(0,len(names)))
    plt.gca().set_xticklabels(names,rotation=0,fontsize=12)
    plt.gca().axhline(0, alpha=0.3,color='k',linestyle='--')
    plt.ylabel(key,fontsize=12)
    plt.xlabel(group, fontsize=12)

    if len(means) == 2:
        # Do significance testing 
        groups = manifest.groupby(group)
        vals = []
        for name, grouped in groups:
            vals.append(grouped[key])
        pval =  ttest_ind(vals[0],vals[1],nan_policy='omit')
        ylim = plt.ylim()[1]
        r = plt.ylim()[1] - plt.ylim()[0]
        sf = .075
        offset = 2 
        plt.plot([0,1],[ylim+r*sf, ylim+r*sf],'k-')
        plt.plot([0,0],[ylim, ylim+r*sf], 'k-')
        plt.plot([1,1],[ylim, ylim+r*sf], 'k-')
     
        if pval[1] < 0.05:
            plt.plot(.5, ylim+r*sf*1.5,'k*')
        else:
            plt.text(.5,ylim+r*sf*1.25, 'ns')

    if type(directory) == type(None):
        directory = global_directory

    if savefig:
        plt.savefig(directory+group_label+"_manifest_"+key+"_groupby_"+group+".png")






