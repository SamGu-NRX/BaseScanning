"""A1 on real iPad/iPhone-class ARKit captures (LaMAR LIN): COLMAP SfM with the device's real ARKit poses.
Modes: prior (ARKit positions as soft priors, A1 recipe), fixed (triangulate with ARKit poses), noprior (image-only SfM,
Sim3-fitted to ARKit cameras afterwards). Intrinsics: the device's per-frame ARKit intrinsics, held fixed.
    python a1_lamar.py <segment id> [modes...]"""
import sys, os, json, time, shutil, numpy as np, pycolmap
sys.path.insert(0, os.path.dirname(__file__))
from lamar_io import *
from laser import render_depth, lookup, backproject
from pairscore import score, fmt
HERE = os.path.dirname(__file__); WORK = os.path.expanduser("~/a6/a1"); THREADS = 4
gt = load_gt()

def setup(seg):
    d = f"{WORK}/{seg['id']}"; img = f"{d}/images"
    os.makedirs(img, exist_ok=True)
    for t in seg["frames"]:
        dst = f"{img}/{t}.jpg"
        if not os.path.exists(dst): os.symlink(f"{ROOT}/raw/{seg['session']}/raw_data/images/{t}.jpg", dst)
    return d

def features(seg, d, ak, K):
    db_p = f"{d}/database.db"
    if os.path.exists(f"{d}/features.json"): return json.load(open(f"{d}/features.json"))
    if os.path.exists(db_p): os.remove(db_p)
    t0 = time.time()
    ext = pycolmap.FeatureExtractionOptions(); ext.max_image_size = 1920; ext.num_threads = THREADS; ext.sift.max_num_features = 8192
    reader = pycolmap.ImageReaderOptions(); reader.camera_model = "PINHOLE"
    pycolmap.extract_features(db_p, f"{d}/images", camera_mode=pycolmap.CameraMode.PER_IMAGE, reader_options=reader,
                              extraction_options=ext, device=pycolmap.Device.cpu)
    t1 = time.time()
    db = pycolmap.Database.open(db_p)
    for im in db.read_all_images():
        ts = im.name[:-4]; w, h, fx, fy, cx, cy = K[ts]
        cam = db.read_camera(im.camera_id); cam.params = [fx, fy, cx, cy]; cam.has_prior_focal_length = True; db.update_camera(cam)
        pp = pycolmap.PosePrior(); pp.corr_data_id = im.data_id; pp.position = ak[ts][1]
        pp.position_covariance = np.eye(3) * 0.3 ** 2; pp.coordinate_system = pycolmap.PosePriorCoordinateSystem.CARTESIAN
        db.write_pose_prior(pp)
    db.close()
    mo = pycolmap.FeatureMatchingOptions(); mo.num_threads = THREADS
    sp = pycolmap.SpatialPairingOptions(); sp.max_num_neighbors = 40; sp.max_distance = 20.0; sp.ignore_z = False
    pycolmap.match_spatial(db_p, matching_options=mo, pairing_options=sp, device=pycolmap.Device.cpu)
    sq = pycolmap.SequentialPairingOptions(); sq.overlap = 10
    pycolmap.match_sequential(db_p, matching_options=mo, pairing_options=sq, device=pycolmap.Device.cpu)
    rep = dict(extract_s=time.time() and t1 - t0, match_s=time.time() - t1, images=len(seg["frames"]))
    json.dump(rep, open(f"{d}/features.json", "w")); return rep

def opts():
    o = pycolmap.IncrementalPipelineOptions(); o.num_threads = THREADS; o.random_seed = 0
    o.ba_refine_focal_length = False; o.ba_refine_principal_point = False; o.ba_refine_extra_params = False
    return o

def best(maps):
    return max(maps.values(), key=lambda r: r.num_reg_images())

def set_prior_sigma(d, sigma):
    db = pycolmap.Database.open(f"{d}/database.db")
    pps = db.read_all_pose_priors(); db.clear_pose_priors()
    for pp in pps: pp.position_covariance = np.eye(3) * sigma ** 2; db.write_pose_prior(pp)
    db.close()

def run_mode(d, mode, ak):
    out = f"{d}/{mode}"
    if os.path.exists(f"{out}/images.bin"): return pycolmap.Reconstruction(out), json.load(open(f"{out}/run.json"))
    tmp = f"{d}/_tmp_{mode}"; shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp); t0 = time.time()
    if mode.startswith("prior") or mode == "noprior":
        o = opts()
        if mode.startswith("prior"):
            # prior = A1 recipe (sigma 0.3 m, robust); prior_sXX = sigma 0.XX m without robust loss
            sigma = 0.3 if mode == "prior" else float(mode.split("_s")[1]) / 100
            set_prior_sigma(d, sigma)
            o.use_prior_position = True; o.use_robust_loss_on_prior_position = mode == "prior"
        maps = pycolmap.incremental_mapping(f"{d}/database.db", f"{d}/images", tmp, options=o)
        rec = best(maps); nm = len(maps)
    else:  # fixed poses: ARKit (fixed) or laser-registered GT on the GT-posed frames only (gt = scorer floor)
        db = pycolmap.Database.open(f"{d}/database.db"); rec = pycolmap.Reconstruction()
        gtv = {v.split("/")[1]: v for v in SEG["gt_views"]}
        cams = {c.camera_id: c for c in db.read_all_cameras()}
        for im in db.read_all_images():
            if mode == "gt":
                if im.name not in gtv: continue
                g = gt[gtv[im.name]]; c = cams[im.camera_id]; c.params = g["cam"][3:7]
                R_cw = np.array(g["R"]); t_cw = np.array(g["t"])
            else:
                R_wc, cc = ak[im.name[:-4]]; R_cw = R_wc.T; t_cw = -R_cw @ cc; c = cams[im.camera_id]
            rec.add_camera_with_trivial_rig(c)
            im2 = pycolmap.Image(name=im.name, camera_id=im.camera_id, image_id=im.image_id)
            rec.add_image_with_trivial_frame(im2, pycolmap.Rigid3d(pycolmap.Rotation3d(R_cw), t_cw))
        db.close()
        rec = pycolmap.triangulate_points(rec, f"{d}/database.db", f"{d}/images", tmp, clear_points=True, options=opts(), refine_intrinsics=False)
        nm = 1
    dt = time.time() - t0; os.makedirs(out, exist_ok=True); rec.write(out); shutil.rmtree(tmp, ignore_errors=True)
    info = dict(mode=mode, seconds=dt, models=nm, registered=rec.num_reg_images(), points3D=rec.num_points3D(),
                reproj_px=rec.compute_mean_reprojection_error())
    json.dump(info, open(f"{out}/run.json", "w")); return rec, info

def evaluate(seg, rec, mode, ak, P_laser):
    names = {im.name: iid for iid, im in rec.images.items()}
    gtv = {v.split("/")[1]: v for v in seg["gt_views"]}
    # recon camera centres vs ARKit (for the noprior Sim3 fit) and vs GT (oracle / shape-only)
    reg = [n for n in names if rec.images[names[n]].has_pose]
    Crec = np.array([rec.images[names[n]].projection_center() for n in reg])
    Cak = np.array([ak[n[:-4]][1] for n in reg])
    gnames = [n for n in reg if n in gtv]
    Cg = np.array([gt_center(gt[gtv[n]]) for n in gnames]); Crg = np.array([rec.images[names[n]].projection_center() for n in gnames])
    s_ak, R_ak, t_ak = umeyama(Crec, Cak)
    s_gt, _, _ = umeyama(Crg, Cg)
    rms_gt = None
    if mode != "noprior":
        s_, R_, t_ = umeyama(Crg, Cg, scale=False); rms_gt = float(np.sqrt(((Crg @ R_.T + t_ - Cg) ** 2).sum(1).mean()))
    # one GT-view observation per 3D point
    Dcache = {}; P, Q, RG = [], [], []
    for pid, p in rec.points3D.items():
        for el in p.track.elements:
            n = rec.images[el.image_id].name
            if n not in gtv: continue
            g = gt[gtv[n]]; _, w, h, fx, fy, cx, cy = g["cam"]
            if n not in Dcache: Dcache[n] = render_depth(P_laser, g["R"], g["t"], [fx, fy, cx, cy], w, h)
            uv = np.array([rec.images[el.image_id].points2D[el.point2D_idx].xy])
            z = lookup(Dcache[n], uv)[0]
            if np.isfinite(z) and z < 20:
                q = backproject(uv, np.array([z]), g["R"], g["t"], [fx, fy, cx, cy])[0]
                P.append(p.xyz); Q.append(q); RG.append(np.linalg.norm(q - gt_center(g)))
            break
    P = np.array(P); Q = np.array(Q)
    os.makedirs(f"{ROOT}/arrays/a1", exist_ok=True)
    sP = s_ak if mode == "noprior" else 1.0
    np.savez_compressed(f"{ROOT}/arrays/a1/{seg['id']}__{mode}.npz", P=sP * P, Q=Q, rng=np.array(RG), s_oracle=s_gt / sP)
    res = {}
    if mode == "noprior": res["arkit_sim3"] = score(s_ak * P, Q)  # image-only shape, scale from real ARKit trajectory
    else: res["metric"] = score(P, Q)
    res["oracle_scale"] = score(s_gt * P, Q)
    res["cams"] = dict(registered=len(reg), gt_views_registered=len(gnames), gt_views=len(gtv),
                       scale_vs_gt_pct=float((1 / s_gt - 1) * 100) if mode != "noprior" else float((s_ak / s_gt - 1) * 100),
                       rigid_rms_to_gt_m=rms_gt)
    return res

if __name__ == "__main__":
    sid = sys.argv[1]; modes = sys.argv[2:] or ["prior", "fixed", "noprior"]
    seg = next(s for s in json.load(open(f"{HERE}/results/segments.json")) if s["id"] == sid); SEG = seg
    ak = load_arkit(seg["session"]); K = load_intrinsics(seg["session"])
    P_laser = np.load(f"{ROOT}/laser/{sid}.npz")["pts"]
    d = setup(seg); fr = features(seg, d, ak, K); print("features", fr, flush=True)
    rp = f"{HERE}/results/a1/{sid}.json"
    allres = json.load(open(rp)) if os.path.exists(rp) else {}
    allres.update({"segment": sid, "features": fr})
    for m in modes:
        rec, info = run_mode(d, m, ak); r = evaluate(seg, rec, m, ak, P_laser); r["run"] = info; allres[m] = r
        k = "arkit_sim3" if m == "noprior" else "metric"
        print(m, info, r["cams"], "long:", fmt(r[k]), "| openings:", fmt(r[k], "0.5-2m (openings)"), "| oracle long:", fmt(r["oracle_scale"]), flush=True)
    os.makedirs(f"{HERE}/results/a1", exist_ok=True)
    json.dump(allres, open(f"{HERE}/results/a1/{sid}.json", "w"), indent=1)
