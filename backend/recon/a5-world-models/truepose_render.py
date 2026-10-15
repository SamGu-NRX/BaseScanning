import sys, json, numpy as np, cv2
sys.path.insert(0,'code')
import splatpose as S
from evalharness.frame import mat4_from_list
w,gtd,Tp,out=sys.argv[1:5]; stems=sys.argv[5].split(',')
T=np.array(json.load(open(Tp)))  # raw splat(metric) -> GT
gt=json.load(open(f'{gtd}/scene.json')); gk={k['id']:k for k in gt['keyframes']}
W=json.load(open(f'{w}/world.json')); msf=W['assets']['splats']['semantics_metadata']['metric_scale_factor']
xyz,rgb,_,_=S.splat_points(S.read_splat_ply(f'{w}/splats_full_res.ply'),msf)
s=np.cbrt(np.linalg.det(T[:3,:3])); Tinv=np.linalg.inv(T)
rows=[]
for st in stems:
    P=mat4_from_list(gk[st]['pose'])  # ARKit c2w in GT
    Pc=Tinv@P  # c2w in splat frame (includes scale)
    R_c2w=Pc[:3,:3]/s; C=Pc[:3,3]
    R_c2w=R_c2w@np.diag([1,-1,-1])  # ARKit -> OpenCV camera axes
    R=R_c2w.T; t=-R@C
    K=np.array(gk[st]['intrinsics'])
    ren,_=S.render(xyz,rgb,R,t,K,gk[st]["w"],gk[st]["h"],max(1,gk[st]["w"]//600))
    ph=cv2.cvtColor(cv2.imread(f'{w}/inputs/{st}.jpg'),cv2.COLOR_BGR2RGB); ph=cv2.resize(ph,(ren.shape[1],ren.shape[0]))
    cv2.putText(ph,st+' photo',(8,24),0,0.7,(255,255,0),2); cv2.putText(ren,'splat from true pose',(8,24),0,0.7,(255,255,0),2)
    r=np.concatenate([ph,ren],1); rows.append(cv2.resize(r,(1200,int(1200*r.shape[0]/r.shape[1]))))
cv2.imwrite(out,cv2.cvtColor(np.concatenate(rows,0),cv2.COLOR_RGB2BGR))
