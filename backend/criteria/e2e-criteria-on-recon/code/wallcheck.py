import sys,json; sys.path.insert(0,'/home/ubuntu/e2e/store/internal/eval-harness')
from evalharness.sceneio import read_ply
from evalharness.frame import align_by_rotations, pose_cv_to_arkit, mat4_from_list, apply
import numpy as np
gt=json.load(open(sys.argv[2])); gkf={k['id']:k for k in gt['keyframes']}
cams=[c for c in json.load(open(sys.argv[1]+'/cameras_model.json')) if c['image'] in gkf]
P=[pose_cv_to_arkit(np.array(c['R_cw']),np.array(c['t_cw'])) for c in cams]; G=[mat4_from_list(gkf[c['image']]['pose']) for c in cams]
S=align_by_rotations(np.array([p[:3,:3] for p in P]),np.array([p[:3,3] for p in P]),np.array([g[:3,:3] for g in G]),np.array([g[:3,3] for g in G]),with_scale=True)
d=read_ply(sys.argv[1]+'/points_model.ply'); X=apply(S,np.c_[d['x'],d['y'],d['z']])
m=(X[:,0]>-12)&(X[:,0]<2.5)&(X[:,1]>0.1)&(X[:,1]<0.9)&(np.abs(X[:,2])<1.5)
z=X[m,2]; print(len(X),'w1 low band',m.sum(),'within 5cm',np.mean(np.abs(z)<0.05).round(3),'10cm',np.mean(np.abs(z)<0.1).round(3), 'p50|z|',np.median(np.abs(z)).round(3))
