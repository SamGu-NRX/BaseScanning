import geometry as G, vlm, json, numpy as np, sys
geo = G.load_eth3d_facade()
ann = json.load(open('support/pr13-eth3d-facade/annotations.json'))
feats = {f['label']: f for f in ann['features']}
frames = sorted({im for f in ann['features'] for im in f['points_px']})
ph = vlm.build_packet('eth3d-facade', [f + '.jpg' for f in frames])
T = G.SceneTools(geo, ph)
idx = {p.frame[:-4]: p.index for p in ph}
for W in [6048, 6205, 6220]:
    H = W*1365/2048
    def ref(label, im, key):
        x,y = feats[label]['points_px'][im][key]
        return {'photo': idx[im], 'x': x*4/W*1000, 'y': y*4/H*1000}
    gaps=[]
    for (l1,l2,truth) in [('w1 window 4','w1 window 5',5.11),('w1 window 3','w1 window 4',5.13),('w1 window 2','w1 window 3',5.45),('w1 window 1','w1 window 2',5.71)]:
        for im in feats[l1]['points_px']:
            if im in feats[l2]['points_px']:
                r = T.measure(ref(l1, im, 'right'), ref(l2, im, 'left'))
                gaps.append((im, l1[-1]+'-'+l2[-1], r.get('horizontal_ft'), truth))
    sills=[]
    for lab in feats:
        for im in feats[lab]['points_px']:
            sills.append((lab[-8:], im, T.locate(**ref(lab,im,'bottom')).get('height_above_ground_ft')))
    print('W',W, 'gaps', gaps)
    print('   sills', sills)
