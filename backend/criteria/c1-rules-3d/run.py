import sys, json, time, yaml, engine3d as E
Y = yaml.safe_load(open('profiles.yaml'))
def prof(n, contract=False):
    p = E._merge(Y['base'], Y['profiles'][n])
    return E._merge(p, Y['contract']) if contract else p
if __name__ == '__main__':
    path = sys.argv[1]; names = sys.argv[2:] or list(Y['profiles'])
    sc = E.load_scene(json.load(open(path)))
    for n in names:
        t = time.time(); r = E.decide(sc, prof(n)); dt = time.time() - t
        b = r['best']
        print(f"{n:9s} {r['decision']:13s} {dt:5.1f}s {r['stats']} ends={r['open_ends_in_reach']}")
        if b: print("   best", b['wall'], b['u'], b['outcome'], b['route_ft'], b['route_corners'], [ (c['id'],c['outcome']) for c in b['checks'] if c['outcome']!='pass'])
