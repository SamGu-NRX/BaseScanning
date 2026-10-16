import sys, json, numpy as np, cv2
sys.path.insert(0,'/cursor/stores/bc-5bc79926-909c-4cfb-aa62-24b3a44735e2/internal/eval-harness')
from evalharness.sceneio import read_ply
from evalharness.frame import apply
w, gtd, out = sys.argv[1], sys.argv[2], sys.argv[3]
T=np.array(json.load(open(f'{w}/inv/T_metric.json')))
d=read_ply(f'{w}/points_metric.ply'); X=apply(T,np.c_[d['x'],d['y'],d['z']])
L=read_ply(f'{gtd}/laser.ply'); Lp=np.c_[L['x'],L['y'],L['z']]
res=0.05; x0,x1,z0,z1=-20,15,-8,25
W,H=int((x1-x0)/res),int((z1-z0)/res)
img=np.full((H,W,3),255,np.uint8)
def draw(P,col,band=(0.3,2.5)):
    P=P[(P[:,1]>band[0])&(P[:,1]<band[1])]
    u=((P[:,0]-x0)/res).astype(int); v=((z1-P[:,2])/res).astype(int)
    ok=(u>=0)&(u<W)&(v>=0)&(v<H); img[v[ok],u[ok]]=col
draw(Lp,(0,0,0)); draw(X,(0,0,255))
cams=json.load(open(f'{w}/cameras.json'))
for c in cams:
    R=np.array(c['R_cw']);t=np.array(c['t_cw']);C=-R.T@t
    Cw=apply(T,C[None])[0]; u=int((Cw[0]-x0)/res); v=int((z1-Cw[2])/res); cv2.circle(img,(u,v),8,(0,160,0),-1)
gt=json.load(open(f'{gtd}/scene.json'))
for k in gt['keyframes']:
    if any(k['id']==c['image'] for c in cams):
        P=np.array(k['pose']).reshape(4,4,order='F'); u=int((P[0,3]-x0)/res); v=int((z1-P[2,3])/res); cv2.circle(img,(u,v),8,(255,0,255),2)
cv2.circle(img,(int(-x0/res),int(z1/res)),10,(255,128,0),3)
cv2.imwrite(out,img)
