#!/usr/bin/env python3
"""Benchmark admitted Germany catalog evidence as disposable SQLite projections."""
from __future__ import annotations
import argparse, gzip, hashlib, json, math, os, resource, sqlite3, statistics, tempfile, time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_ID=1_212_564_821; FACTORS=(1,2,5); WARM=1000; COLD=25

def load(p:Path)->Any: return json.loads(p.read_text(encoding="utf-8"))
def canon(v:Any)->bytes: return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()
def digest_file(p:Path)->str:
 d=hashlib.sha256();
 with p.open("rb") as f:
  for chunk in iter(lambda:f.read(1<<20),b""): d.update(chunk)
 return d.hexdigest()
def pct(v:list[float],q:float)->float:
 if not v:return 0.0
 s=sorted(v); return s[min(len(s)-1,max(0,math.ceil(q*len(s))-1))]
def gtin_ok(v:str)->bool:
 if len(v)!=14 or not v.isascii() or not v.isdigit(): return False
 total=sum(int(c)*(3 if i%2==0 else 1) for i,c in enumerate(reversed(v[:-1])))
 return (10-total%10)%10==int(v[-1])
valid_gtin=gtin_ok
def modeled_gtin(n:int)->str:
 body=f"99{n:011d}"; total=sum(int(c)*(3 if i%2==0 else 1) for i,c in enumerate(reversed(body)))
 return body+str((10-total%10)%10)
def gz_size(p:Path)->int:
 with p.open("rb") as src, tempfile.TemporaryFile() as out:
  with gzip.GzipFile(fileobj=out,mode="wb",compresslevel=9,mtime=0) as z:
   for chunk in iter(lambda:src.read(1<<20),b""): z.write(chunk)
  return out.tell()
def db_bytes(c:sqlite3.Connection)->int:
 return int(c.execute("pragma page_count").fetchone()[0])*int(c.execute("pragma page_size").fetchone()[0])
def prep(c:sqlite3.Connection)->None:
 c.execute("pragma journal_mode=off"); c.execute("pragma synchronous=off"); c.execute("pragma temp_store=file")
 c.execute("pragma page_size=4096"); c.execute(f"pragma application_id={APP_ID}"); c.execute("pragma user_version=1")

def products(e:dict[str,Any])->list[dict[str,Any]]:
 ids={x["id"]:x for x in e.get("identities",[])}; ing={x["id"]:x for x in e.get("ingredients",[])}; out=[]
 for s in e.get("currentSelections",[]):
  i=ids.get(s.get("identityObservationID")); g=ing.get(s.get("ingredientObservationID")); code=str(s.get("gtin",""))
  if not i or s.get("market")!="DE" or not gtin_ok(code): continue
  out.append({"gtin":code,"market":"DE","name":str(i.get("name","")),"brand":i.get("brand"),"sourceKey":str(i.get("sourceKey","")),
   "sourceRecordID":str(i.get("sourceRecordID","")),"retrievedAt":str(i.get("retrievedAt","")),"sourceModifiedAt":i.get("sourceModifiedAt"),
   "ingredientsText":"" if not g else str(g.get("ingredientsText","")),"languageCode":"und" if not g else str(g.get("languageCode","und")),
   "contentHash":None if not g else g.get("contentHash"),"ingredientObservedAt":None if not g else g.get("observedAt"),
   "ingredientRetrievedAt":None if not g else g.get("retrievedAt"),"identityEvidenceID":i.get("id"),"ingredientEvidenceID":None if not g else g.get("id")})
 return sorted(out,key=lambda x:x["gtin"])
def retailers(e:dict[str,Any],allowed:set[str])->list[dict[str,Any]]:
 out=[]
 for x in e.get("retailerEvidence",[]):
  code=str(x.get("gtin",""))
  if code not in allowed or x.get("market")!="DE":continue
  out.append({k:x.get(k) for k in ("id","gtin","retailerKey","kind","observedAt","retrievedAt","sourceKey","sourceRecordID")})
 return sorted(out,key=lambda x:(str(x["gtin"]),str(x["retailerKey"]),str(x["observedAt"]),str(x["id"])))
def latest(rows:list[dict[str,Any]])->list[dict[str,Any]]:
 out={}
 for r in rows:
  k=(r["gtin"],r["retailerKey"]); stamp=str(r.get("observedAt") or r.get("retrievedAt") or "")
  if k not in out or (stamp,str(r["id"]))>(str(out[k].get("observedAt") or out[k].get("retrievedAt") or ""),str(out[k]["id"])): out[k]=r
 return sorted(out.values(),key=lambda x:(str(x["gtin"]),str(x["retailerKey"])))
def semantic(rows:list[dict[str,Any]])->str:
 keys=("gtin","market","name","brand","sourceKey","sourceRecordID","ingredientsText","languageCode","contentHash")
 return hashlib.sha256(canon([{k:r[k] for k in keys} for r in rows])).hexdigest()

def build_audit(path:Path,rows:list[dict[str,Any]],ret:list[dict[str,Any]],fixture:dict[str,Any])->dict[str,Any]:
 t=time.perf_counter(); c=sqlite3.connect(path); prep(c); c.executescript('''
 create table products(gtin text primary key,market text,name text,brand text,source_key text,source_record_id text,identity_id text);
 create table ingredients(id text primary key,gtin text,text text,language text,content_hash text,observed_at text,retrieved_at text);
 create table retailer_observations(id text primary key,gtin text,retailer_key text,kind text,observed_at text,retrieved_at text,source_key text,source_record_id text);
 create table representative_assessments(id text primary key,gtin text,status text,methodology text,assessed_at text,synthetic integer check(synthetic=1));
 create table representative_reasons(assessment_id text,position integer,code text,severity text,title text,detail text,synthetic integer check(synthetic=1),primary key(assessment_id,position));''')
 c.executemany("insert into products values(?,?,?,?,?,?,?)",[(r["gtin"],r["market"],r["name"],r["brand"],r["sourceKey"],r["sourceRecordID"],r["identityEvidenceID"]) for r in rows])
 c.executemany("insert into ingredients values(?,?,?,?,?,?,?)",[(r["ingredientEvidenceID"] or "missing:"+r["gtin"],r["gtin"],r["ingredientsText"],r["languageCode"],r["contentHash"],r["ingredientObservedAt"],r["ingredientRetrievedAt"]) for r in rows])
 c.executemany("insert into retailer_observations values(?,?,?,?,?,?,?,?)",[(r["id"],r["gtin"],r["retailerKey"],r["kind"],r["observedAt"],r["retrievedAt"],r["sourceKey"],r["sourceRecordID"]) for r in ret])
 ass=fixture.get("assessments",[]); c.executemany("insert into representative_assessments values(?,?,?,?,?,1)",[(a["id"],a["gtin"],a["status"],a["methodologyVersion"],a["assessedAt"]) for a in ass])
 reasons=[]
 for a in ass:
  reasons += [(a["id"],i,r["code"],r["severity"],r["title"],r["detail"]) for i,r in enumerate(a.get("reasons",[]))]
 c.executemany("insert into representative_reasons values(?,?,?,?,?,?,1)",reasons); before=db_bytes(c)
 c.executescript("create index idx_products_source on products(source_key,source_record_id);create index idx_ingredients_gtin on ingredients(gtin);create index idx_retailer_gtin on retailer_observations(gtin,retailer_key,observed_at desc);")
 after=db_bytes(c); c.commit(); c.execute("vacuum"); c.close()
 return {"beforeIndexBytes":before,"afterIndexBytesBeforeVacuum":after,"indexOverheadBytes":after-before,"vacuumBytes":path.stat().st_size,"gzipBytes":gz_size(path),"buildSeconds":round(time.perf_counter()-t,3),"representativeAssessments":len(ass),"representativeReasons":len(reasons)}

def build_runtime(path:Path,rows:list[dict[str,Any]],ret:list[dict[str,Any]],basic:list[dict[str,Any]],factor:int)->dict[str,Any]:
 t=time.perf_counter(); c=sqlite3.connect(path); prep(c); c.executescript('''
 create table products(gtin text primary key,source_gtin text,market text,name text,brand text,ingredients_text text,language_code text,content_hash text,source_key text,source_record_id text,retrieved_at text,is_growth_model integer) without rowid;
 create table retailer_summary(gtin text,retailer_key text,kind text,observed_at text,primary key(gtin,retailer_key)) without rowid;
 create table basic_exclusion(gtin text,market text,policy_version text,reason text,primary key(gtin,market)) without rowid;''')
 existing={r["gtin"] for r in rows}; seq=0; batch=[]
 for shard in range(factor):
  for r in rows:
   if shard==0: code=r["gtin"]; modeled=0
   else:
    while True:
     seq+=1; code=modeled_gtin(seq)
     if code not in existing: break
    modeled=1
   batch.append((code,r["gtin"],r["market"],r["name"],r["brand"],r["ingredientsText"],r["languageCode"],r["contentHash"],r["sourceKey"],r["sourceRecordID"],r["retrievedAt"],modeled))
   if len(batch)>=5000:c.executemany("insert into products values(?,?,?,?,?,?,?,?,?,?,?,?)",batch);batch=[]
 if batch:c.executemany("insert into products values(?,?,?,?,?,?,?,?,?,?,?,?)",batch)
 if factor==1:
  c.executemany("insert into retailer_summary values(?,?,?,?)",[(r["gtin"],r["retailerKey"],r["kind"],r["observedAt"] or r["retrievedAt"]) for r in ret])
  c.executemany("insert into basic_exclusion values(?,?,?,?)",[(r["gtin"],r["market"],r["policyVersion"],r["reasonCode"]) for r in basic])
 before=db_bytes(c); c.execute("create index idx_products_source on products(source_key,source_record_id)"); after=db_bytes(c); c.commit(); c.execute("vacuum"); c.close()
 uri=f"file:{path.as_posix()}?mode=ro"; ro=sqlite3.connect(uri,uri=True); probes=[r["gtin"] for r in rows[:WARM]]
 plan=[str(x) for x in ro.execute("explain query plan select * from products where gtin=?",(probes[0],)).fetchall()]; warm=[]
 for i in range(WARM):
  tick=time.perf_counter_ns(); found=ro.execute("select gtin,name,brand,ingredients_text,language_code,source_key,source_record_id from products where gtin=?",(probes[i%len(probes)],)).fetchone(); warm.append((time.perf_counter_ns()-tick)/1e6)
  if not found: raise AssertionError("known GTIN missing")
 bplan=[]; blat=[]
 if factor==1 and basic:
  bplan=[str(x) for x in ro.execute("explain query plan select reason from basic_exclusion where gtin=? and market='DE'",(basic[0]["gtin"],)).fetchall()]
  for i in range(WARM):
   tick=time.perf_counter_ns(); found=ro.execute("select reason from basic_exclusion where gtin=? and market='DE'",(basic[i%len(basic)]["gtin"],)).fetchone(); blat.append((time.perf_counter_ns()-tick)/1e6)
   if not found: raise AssertionError("known exclusion missing")
 ro.close(); cold=[]
 for code in probes[:min(COLD,len(probes))]:
  tick=time.perf_counter_ns(); q=sqlite3.connect(uri,uri=True); found=q.execute("select gtin,name,brand,ingredients_text from products where gtin=?",(code,)).fetchone(); q.close(); cold.append((time.perf_counter_ns()-tick)/1e6)
  if not found: raise AssertionError("known GTIN missing after open")
 return {"growthFactor":factor,"productRows":len(rows)*factor,"modeledGrowthRows":len(rows)*(factor-1),"beforeIndexBytes":before,"afterIndexBytesBeforeVacuum":after,"indexOverheadBytes":after-before,"vacuumBytes":path.stat().st_size,"gzipBytes":gz_size(path),"buildSeconds":round(time.perf_counter()-t,3),"queryPlan":plan,"basicExclusionQueryPlan":bplan,"basicExclusionLookupMs":None if not blat else {"p50":round(pct(blat,.5),4),"p95":round(pct(blat,.95),4),"p99":round(pct(blat,.99),4)},"warmLookupMs":{"p50":round(pct(warm,.5),4),"p95":round(pct(warm,.95),4),"p99":round(pct(warm,.99),4)},"firstLookupAfterOpenMs":{"p50":round(pct(cold,.5),4),"p95":round(pct(cold,.95),4),"p99":round(pct(cold,.99),4)}}

def ingredient_metrics(rows:list[dict[str,Any]])->dict[str,Any]:
 sizes=[len(r["ingredientsText"].encode()) for r in rows if r["ingredientsText"]]; langs=Counter(r["languageCode"] for r in rows if r["ingredientsText"])
 return {"withIngredients":len(sizes),"missingIngredients":len(rows)-len(sizes),"coveragePercent":round(100*len(sizes)/len(rows),3),"languageCounts":dict(sorted(langs.items())),"ingredientTextBytes":{"average":round(statistics.fmean(sizes),2) if sizes else 0,"p50":pct([float(x) for x in sizes],.5),"p95":pct([float(x) for x in sizes],.95)}}
def weekly(rows:list[dict[str,Any]],ret:list[dict[str,Any]],anchor_raw:str)->dict[str,Any]:
 try: anchor=datetime.fromisoformat(anchor_raw.replace("Z","+00:00"))
 except ValueError:return {"method":"unavailable","productRows":0,"retailerObservations":0,"canonicalBytes":0}
 cutoff=anchor-timedelta(days=7); changed=[]; observed=[]
 for r in rows:
  raw=r.get("sourceModifiedAt")
  try: stamp=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
  except ValueError: continue
  if stamp>=cutoff:changed.append((r["gtin"],raw,r["contentHash"]))
 for r in ret:
  raw=r.get("observedAt") or r.get("retrievedAt")
  try: stamp=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
  except ValueError: continue
  if stamp>=cutoff:observed.append((r["gtin"],r["retailerKey"],raw))
 return {"method":"single-snapshot 7-day sourceModifiedAt/observedAt proxy","windowStart":cutoff.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),"windowEnd":anchor.astimezone(timezone.utc).isoformat().replace("+00:00","Z"),"productRows":len(changed),"retailerObservations":len(observed),"canonicalBytes":len(canon({"products":changed,"retailerObservations":observed}))}

def run(a:argparse.Namespace)->dict[str,Any]:
 oe,sel,oq,om=load(a.off_evidence),load(a.off_selection),load(a.off_quality),load(a.off_metadata); pe,pq,pm=load(a.open_prices_evidence),load(a.open_prices_quality),load(a.open_prices_metadata); fixture=load(a.review_fixture)
 rows=products(oe)
 if not rows: raise ValueError("no selected Germany products")
 ret=retailers(pe,{r["gtin"] for r in rows}); last=latest(ret); basic=[x for x in sel.get("basicExclusions",[]) if x.get("market")=="DE" and gtin_ok(str(x.get("gtin","")))]
 a.work_dir.mkdir(parents=True,exist_ok=True); auditp=a.work_dir/"catalog-current-audit.sqlite3"; auditp.unlink(missing_ok=True); audit=build_audit(auditp,rows,ret,fixture); runtime=[]; one=None
 for f in FACTORS:
  p=a.work_dir/f"catalog-runtime-{f}x.sqlite3"; p.unlink(missing_ok=True); runtime.append(build_runtime(p,rows,last,basic,f)); one=p if f==1 else one
  if f!=1:p.unlink(missing_ok=True)
 nop=a.work_dir/"catalog-runtime-1x-no-basic.sqlite3"; no=build_runtime(nop,rows,last,[],1); nop.unlink(missing_ok=True); cost={"rows":len(basic),"vacuumByteDelta":runtime[0]["vacuumBytes"]-no["vacuumBytes"],"gzipByteDelta":runtime[0]["gzipBytes"]-no["gzipBytes"],"lookupMs":runtime[0]["basicExclusionLookupMs"],"queryPlan":runtime[0]["basicExclusionQueryPlan"]}
 assert one; ro=sqlite3.connect(f"file:{one.as_posix()}?mode=ro",uri=True); roundtrip=[{"gtin":x[0],"market":x[1],"name":x[2],"brand":x[3],"sourceKey":x[4],"sourceRecordID":x[5],"ingredientsText":x[6],"languageCode":x[7],"contentHash":x[8]} for x in ro.execute("select gtin,market,name,brand,source_key,source_record_id,ingredients_text,language_code,content_hash from products where is_growth_model=0 order by gtin")];ro.close(); sem=semantic(rows); rd=hashlib.sha256(canon(roundtrip)).hexdigest()
 if sem!=rd:raise AssertionError("semantic round trip mismatch")
 opcomp=sum(int(x.get("compressedBytes",0)) for x in pm.get("upstreamExports",{}).values())
 report={"schemaVersion":1,"architectureCandidate":"A-bundled-sqlite","scope":"Germany detailed catalog selected by accepted v1 policy; Open Prices is observational only","sourceSnapshots":{"openFoodFacts":{"snapshotID":om.get("snapshotID"),"retrievedAt":om.get("retrievedAt"),"transportSha256":om.get("transportSha256"),"transportCompressedBytes":int(om.get("transportBytes",0)),"expandedBytesScanned":int(om.get("expandedBytes",0)),"recordsExamined":om.get("recordsExamined"),"germanyRecordsEmitted":om.get("recordsEmitted"),"sourceSchemaVersions":om.get("sourceSchemaVersions"),"expectedProductSchemaVersion":om.get("expectedProductSchemaVersion"),"apiVersion":om.get("apiVersion"),"tagSchema":om.get("tagSchema"),"selectionPolicyVersion":oq.get("selectionPolicyVersion"),"sourcePolicySha256":digest_file(a.off_source_policy),"selectionPolicySha256":digest_file(a.selection_policy)},"openPrices":{"snapshotID":pm.get("snapshotID"),"retrievedAt":pm.get("retrievedAt"),"upstreamCompressedBytes":opcomp,"projectedPayloadBytes":int(pm.get("payloadBytes",0)),"upstreamExports":pm.get("upstreamExports"),"aliasVersion":pq.get("aliasVersion"),"sourcePolicySha256":digest_file(a.open_prices_source_policy),"retailerAliasesSha256":digest_file(a.retailer_aliases),"noCompletenessClaim":True}},"selection":sel.get("report",{}),"realCatalog":{"uniqueValidSelectedGTINs":len(rows),"ingredientMetrics":ingredient_metrics(rows),"retailerObservationRowsForSelectedProducts":len(ret),"retailerSummaryRows":len(last),"retailerCounts":dict(sorted(Counter(str(r["retailerKey"]) for r in ret).items())),"basicExclusionRows":len(basic),"commonSemanticSha256":sem,"roundTripSemanticSha256":rd},"projectionMeasurements":{"rawStaged":{"openFoodFactsCompressedBytes":int(om.get("transportBytes",0)),"openFoodFactsExpandedBytesScanned":int(om.get("expandedBytes",0)),"openPricesCompressedBytes":opcomp,"openPricesProjectedPayloadBytes":int(pm.get("payloadBytes",0)),"note":"raw/staged source history remains outside the iOS bundle"},"currentAudit":audit,"minimalRuntime":runtime,"basicExclusionIndexCost":cost},"representativeWeeklyRefresh":weekly(rows,ret,str(om.get("retrievedAt",""))),"licenseBoundary":{"openDataPartition":"ODbL-compatible Open Food Facts/Open Prices projection","futureIncompatibleOfficialFeeds":"separate catalog partition unless legal review permits combination","unauthorizedRetailerScrapingUsed":False,"productImageBinariesIncluded":False},"measurementEnvironment":{"platform":os.uname().sysname,"machine":os.uname().machine,"sqliteVersion":sqlite3.sqlite_version,"pythonVersion":os.sys.version.split()[0],"peakProcessRSSKiB":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)},"interpretationGuardrails":["2x/5x extra rows are storage/index models, not real products.","Weekly refresh is a single-snapshot seven-day timestamp proxy, not a two-snapshot delta.","Open Prices observations do not imply retailer inventory completeness or formulation freshness.","Synthetic review fixtures measure schema overhead only and are excluded from real-product counts."]}
 a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8");return report

def parser()->argparse.ArgumentParser:
 p=argparse.ArgumentParser(description=__doc__)
 for flag in ("off-evidence","off-selection","off-quality","off-metadata","open-prices-evidence","open-prices-quality","open-prices-metadata","review-fixture","off-source-policy","open-prices-source-policy","selection-policy","retailer-aliases","work-dir","report"):p.add_argument("--"+flag,type=Path,required=True)
 return p
def main()->None:
 r=run(parser().parse_args());one=r["projectionMeasurements"]["minimalRuntime"][0];print(json.dumps({"selectedGTINs":r["realCatalog"]["uniqueValidSelectedGTINs"],"runtimeBytes":one["vacuumBytes"],"runtimeGzipBytes":one["gzipBytes"],"warmP95Ms":one["warmLookupMs"]["p95"],"firstOpenP95Ms":one["firstLookupAfterOpenMs"]["p95"]},sort_keys=True,separators=(",",":")))
if __name__=="__main__":main()
