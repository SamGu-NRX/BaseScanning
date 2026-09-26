import sys, json, numpy as np, cv2
sys.path.insert(0,'code')
import splatpose as S
from register_inputs import rot
h=sys.argv[1]; out=sys.argv[2]
W=json.load(open(f'{h}/world.json')); msf=W['assets']['splats']['semantics_metadata']['metric_scale_factor']
xyz,rgb,_,_=S.splat_points(S.read_splat_ply(f'{h}/splats_full_res.ply'),msf)
K=np.array([500,500,480,270.]); tiles=[]
for y in range(0,360,45):
    ren,_=S.render(xyz,rgb,rot(y,0),np.zeros(3),K,960,540,2); cv2.putText(ren,f'yaw {y}',(8,24),0,0.7,(255,0,0),2); tiles.append(ren)
g=np.concatenate([np.concatenate(tiles[i:i+4],1) for i in (0,4)],0)
cv2.imwrite(out,cv2.cvtColor(g,cv2.COLOR_RGB2BGR))
