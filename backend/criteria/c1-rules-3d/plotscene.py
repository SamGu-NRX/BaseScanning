import sys, json, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt, engine3d as E
from shapely import get_coordinates
d=json.load(open(sys.argv[1])); sc=E.load_scene(d); g=sc.ground
fig,ax=plt.subplots(figsize=(14,10))
ext=[g.x0,g.x0+g.h.shape[1]*g.res,g.z0,g.z0+g.h.shape[0]*g.res]
ax.imshow(g.h,origin='lower',extent=ext,cmap='viridis',alpha=.7)
def draw(geom,**k):
    for p in getattr(geom,'geoms',[geom]):
        if p.geom_type=='Polygon': x,y=p.exterior.xy; ax.plot(x,y,**k)
        elif p.geom_type in('LineString',): x,y=p.xy; ax.plot(x,y,**k)
draw(sc.observed_ground,color='r',lw=.6)
draw(sc.interior,color='k',lw=1,ls='--')
for o in sc.obstacles: draw(o.geom,color='m',lw=1)
for o in sc.overheads: draw(o.geom,color='c',lw=.5)
for w in sc.walls.values():
    ax.plot(*zip(w.pt(0),w.pt(w.L)),'k-',lw=3); ax.text(*w.pt(w.L/2,3),w.id); ax.arrow(*w.pt(w.L/2),*(w.outward*2),head_width=.5)
for o in sc.objects: draw(o.geom,color='orange',lw=4)
ax.plot(sc.meter_pos[0],sc.meter_pos[2],'r*',ms=15)
ax.set_aspect('equal'); plt.savefig(sys.argv[2],dpi=90)
