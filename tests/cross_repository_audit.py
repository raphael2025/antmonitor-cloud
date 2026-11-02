"""Isolated real site -> wire producer -> cloud HTTP routes reconciliation."""
import json, os, sys, tempfile, time
from pathlib import Path
from unittest.mock import patch
from time import perf_counter

import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("mode",choices=("site","cloud"))
parser.add_argument("--site",type=Path,required=True,help="Path to the AntMonitor checkout")
parser.add_argument("--cloud",type=Path,required=True,help="Path to the AntMonitor Cloud checkout")
parser.add_argument("--payload",type=Path,required=True,help="Temporary JSON payload file between the two isolated processes")
args=parser.parse_args()
SITE=args.site.resolve();CLOUD=args.cloud.resolve();OUT=args.payload.resolve();mode=args.mode
if mode=='site':
    sys.path[:0]=[str(SITE),str(SITE/'tests')]
    import yaml
    tmp=Path(tempfile.mkdtemp(prefix='ant-data-site-'))
    cfg=tmp/'config.yaml'
    cfg.write_text(yaml.safe_dump({'server':{'allowed_hosts':['testserver']},'auth':{'enabled':True,'users':[{'username':'audit','password':'isolated-audit-password','role':'admin'}]},'db':{'path':str(tmp/'bootstrap.db')},'database':{'path':str(tmp/'bootstrap.db')},
        'logging':{'file':str(tmp/'audit.log'),'level':'ERROR'}, 'public_api':{'token':'isolated-audit-token'}}))
    os.environ['MINER_CONFIG']=str(cfg)
    import server,db,cloud_report
    from conftest import insert_scan,rec
    from fastapi.testclient import TestClient
    server.app.dependency_overrides[server.require_viewer]=lambda:{'username':'audit','role':'admin'}
    client=TestClient(server.app)
    now=int(time.time()); end=now//600*600
    result={'now':now,'end':end,'cases':[]}
    class Session:
        def close(self): pass
    def capture(name):
        sent=[]
        def post(c,path,payload,sess): sent.append({'path':path,'body':payload})
        def stop(gen,seconds): cloud_report._GEN+=1
        cloud_report._GEN=71
        with patch.object(cloud_report,'_session',return_value=Session()), patch.object(cloud_report,'_post',side_effect=post), patch.object(cloud_report,'_sleep',side_effect=stop):
            cloud_report._loop(71,{'site_name':name,'customer_hours':24},name,server._build_cloud_summary,
                               server._customer_report_packet)
        assert len(sent)==2
        return sent
    with patch('time.time',return_value=now):
      for name,n in [('audit-a',1330),('audit-b',539)]:
        conn=db.init_db(':memory:');server.SVC.conn=conn;server._cache.clear()
        rows=[rec(f'10.{i//65536}.{(i//254)%254}.{i%254+1}',worker='shared' if i<100 else name,hr=293,power=3000) for i in range(n)]
        started=perf_counter()
        for stamp in range(end-86400,end+1,300):
            for r in rows:r['measured_ts']=stamp
            sid=insert_scan(conn,stamp,'quick',rows)
        db.upsert_known_miners(conn,rows,end)
        scan={'scan_id':sid,'ts':end,'kind':'quick'}
        server.SVC.latest=lambda:(scan,rows)
        summary=client.get('/api/summary').json()
        public=client.get('/api/public/summary',headers={'Authorization':'Bearer isolated-audit-token'}).json()
        report=client.get('/api/reports/customers?hours=24').json()
        assert summary.get('total_hashrate_th')==public.get('total_hashrate_ths')==n*293,(summary,public)
        assert sum(r['avg_hashrate_ths'] for r in report['customers'])==n*293
        assert sum(r['power_kwh'] for r in report['customers'])==n*3*24
        assert all(r['coverage_pct']==100 and r['power_coverage_pct']==100 for r in report['customers'])
        sent=capture(name)
        result['cases'].append({'name':name,'n':n,'sent':sent})
        print(json.dumps({'site':name,'machines':n,'snapshots':289,'rate_ths':n*293,'energy_kwh':n*72,'seconds':round(perf_counter()-started,2)}),flush=True)
        if name=='audit-a':
            for r in rows:r['measured_ts']=now-1800
            scan={'scan_id':sid+1,'ts':now,'kind':'full'}
            stale=capture(name)[0]
            assert stale['body']['hashrate_ths'] is None and stale['body']['unknown']==n
            result['stale_a']=stale
        conn.close()
    OUT.write_text(json.dumps(result,ensure_ascii=False))
else:
    sys.path[:0]=[str(CLOUD),str(CLOUD/'tests')]
    import pytest
    from test_site_management import cloud,login
    data=json.loads(OUT.read_text())
    mp=pytest.MonkeyPatch()
    gen=cloud.__wrapped__(Path(tempfile.mkdtemp(prefix='ant-data-cloud-')),mp)
    server,client=next(gen)
    def send(p):
        r=client.post(p['path'],headers={'Authorization':'Bearer reporting-test-token'},json=p['body'])
        assert r.status_code==200,r.text
        return r.json()
    try:
      with patch('time.time',return_value=data['now']):
        for case in data['cases']:
            for packet in case['sent']:send(packet)
        login(client)
        overview=client.get('/api/overview').json()
        customers=client.get('/api/customers').json()
        assert overview['totals']['machines_total']==1869,overview
        assert overview['totals']['hashrate_ths']==547617,overview
        assert overview['totals']['power_kw']==5607
        assert overview['totals']['customers']==3
        assert overview['totals']['customer_site_pairs']==4
        assert sum(r['avg_hashrate_ths'] for r in customers['by_worker'])==547617,customers
        assert sum(r['power_kwh'] for r in customers['by_worker'])==134568
        shared=next(r for r in customers['by_worker'] if r['worker']=='shared')
        assert shared['avg_hashrate_ths']==shared['hashrate_ths']==58600
        assert shared['power_kwh']==14400
        assert shared['window_alignment']==shared['billing_window_alignment']=='aligned'
        send(data['stale_a'])
        incomplete=client.get('/api/overview').json()
        assert incomplete['totals']['hashrate_ths'] is None
        assert incomplete['observed_totals']['hashrate_ths']==157927
        current=client.get('/api/customers').json()
        shared=next(r for r in current['by_worker'] if r['worker']=='shared')
        assert shared['hashrate_ths'] is None and shared['observed_hashrate_ths']==29300
        assert shared['avg_hashrate_ths']==58600 # Historical aligned window is still valid.
        assert send(data['cases'][0]['sent'][0])['stored'] is False # Delayed older packet cannot undo unknown.
        print(json.dumps({'full_machines':1869,'full_hashrate_ths':547617,'full_power_kw':5607,'full_24h_energy_kwh':134568,
          'unique_accounts':3,'site_account_pairs':4,'stale_site_full_total':None,'trusted_remaining_ths':157927,
          'shared_live_after_stale':None,'shared_24h_average_ths':58600,'delayed_old_packet':'rejected'}),flush=True)
    finally:
        try:next(gen)
        except StopIteration:pass
        mp.undo()
