# First load the database of sessions, then find if we have models fit for each, then do 
# some extraction and save the results.
import os
import numpy as np
import pandas as pd
from glob import glob
from licking_behavior.src import licking_model as mo
import sys; sys.path.append('/home/nick.ponvert/src/nick-allen')
import mp

vb_sessions = pd.read_hdf('/home/nick.ponvert/nco_home/data/vb_sessions.h5', key='df')
storage_dir = '/home/nick.ponvert/nco_home/cluster_jobs/20190708_fit_training_history'

def find_model(row):
    if pd.isnull(row['ophys_experiment_id']):
        session_str = 'model_behavior_{}*'.format(int(row['behavior_session_id']))
    else:
        session_str = 'model_ophys_{}*'.format(int(row['ophys_experiment_id']))

    models = glob(os.path.join(storage_dir, session_str))
    return models

vb_sessions['model_fits'] = mp.parallelize_on_rows(vb_sessions, find_model)

