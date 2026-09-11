#!/usr/bin/env python3
"""Build, sign, and independently verify signed catalog-module envelopes."""
from __future__ import annotations
import argparse, base64, datetime, hashlib, json, os, pathlib, re, sqlite3, subprocess, tempfile
from typing import Any

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
MARKET_RE = re.compile(r"^[A-Z]{2}$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
RELEASE_RE = re.compile(r"^catalog-[a-z]{2}-[A-Za-z0-9._-]{1,120}$")
MAX_DATABASE_BYTES = 500 * 1024 * 1024
PRIVATE_DER_PREFIX = bytes.fromhex("302e020100300506032b657004220420")
PUBLIC_DER_PREFIX = bytes.fromhex("302a300506032b6570032100")

class ContractError(ValueError): pass

def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()

def load_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))

def require_regular_file(path: pathlib.Path, *, maximum: int | None = None, label: str = "file") -> int:
    if path.is_symlink() or not path.is_file(): raise ContractError(f"{label} must be a regular non-symlink file")
    size=path.stat().st_size
    if size <= 0 or (maximum is not None and size > maximum): raise ContractError(f"{label} size is outside accepted bounds")
    return size

def sha256_file(path: pathlib.Path) -> str:
    require_regular_file(path,label=str(path))
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024), b""): h.update(chunk)
    return h.hexdigest()

def _require_exact(obj: dict[str, Any], fields: set[str], label: str) -> None:
    if set(obj) != fields: raise ContractError(f"{label} fields differ from v1 contract")

def _require_token(value: Any, regex: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or not regex.fullmatch(value): raise ContractError(f"invalid {label}")
    return value

def _require_sha(value: Any, label: str) -> str:
    return _require_token(value, SHA_RE, label)

def _require_int(value: Any, low: int, high: int | None, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high): raise ContractError(f"invalid {label}")
    return value

def validate_trust_policy(policy: Any) -> dict[str, Any]:
    if not isinstance(policy, dict): raise ContractError("trust policy must be object")
    _require_exact(policy, {"schemaVersion","keys","revokedModuleIDs","revokedDatabaseSha256"}, "trust policy")
    if policy["schemaVersion"] != 1: raise ContractError("unsupported trust policy schema")
    keys=policy["keys"]
    if not isinstance(keys, list) or len(keys)>16: raise ContractError("invalid trust key list")
    seen=set()
    for key in keys:
        if not isinstance(key,dict): raise ContractError("trust key must be object")
        _require_exact(key,{"keyID","publicKeyBase64","state"},"trust key")
        kid=_require_token(key["keyID"],KEY_RE,"keyID")
        if kid in seen: raise ContractError("duplicate keyID")
        seen.add(kid)
        if key["state"] not in {"active","retired","revoked"}: raise ContractError("invalid key state")
        try: raw=base64.b64decode(key["publicKeyBase64"],validate=True)
        except Exception as exc: raise ContractError("invalid public key base64") from exc
        if len(raw)!=32: raise ContractError("Ed25519 public key must be 32 bytes")
    for module in policy["revokedModuleIDs"]:
        if not isinstance(module,str) or not re.fullmatch(r"^[A-Z]{2}-[A-Za-z0-9._-]{1,120}$",module): raise ContractError("invalid revoked module id")
    for digest in policy["revokedDatabaseSha256"]: _require_sha(digest,"revoked database digest")
    return policy

def validate_manifest(manifest: Any) -> dict[str, Any]:
    if not isinstance(manifest,dict): raise ContractError("manifest must be object")
    fields={"schemaVersion","moduleID","market","catalogVersion","runtimeSchemaVersion","methodologyVersion","minimumAppVersion","maximumAppVersion","database","catalogManifest","attribution","counts","coverage","sourceSnapshotIdentity","releaseIdentity","compressedBytes","installedBytes","publishedAt","signingKeyID","supersedesModuleID"}
    _require_exact(manifest,fields,"module manifest")
    if manifest["schemaVersion"]!=1: raise ContractError("unsupported module manifest schema")
    market=_require_token(manifest["market"],MARKET_RE,"market")
    module=_require_token(manifest["moduleID"],re.compile(r"^[A-Z]{2}-[A-Za-z0-9._-]{1,120}$"),"moduleID")
    if not module.startswith(market+"-"): raise ContractError("moduleID market prefix mismatch")
    _require_token(manifest["releaseIdentity"],RELEASE_RE,"releaseIdentity")
    if not manifest["releaseIdentity"].startswith("catalog-"+market.lower()+"-"): raise ContractError("release identity market mismatch")
    _require_token(manifest["catalogVersion"],VERSION_RE,"catalogVersion")
    for name, maximum in (("methodologyVersion",120),("sourceSnapshotIdentity",200)):
        if not isinstance(manifest[name],str) or not manifest[name] or len(manifest[name].encode("utf-8"))>maximum: raise ContractError(f"invalid {name}")
    if not isinstance(manifest["publishedAt"],str) or len(manifest["publishedAt"].encode("utf-8"))>64: raise ContractError("invalid publishedAt")
    try:
        datetime.datetime.fromisoformat(manifest["publishedAt"].replace("Z","+00:00"))
    except ValueError as exc:
        raise ContractError("publishedAt must be RFC3339/ISO-8601") from exc
    _require_token(manifest["minimumAppVersion"],VERSION_RE,"minimumAppVersion")
    _require_token(manifest["maximumAppVersion"],VERSION_RE,"maximumAppVersion")
    if semver_key(manifest["minimumAppVersion"]) > semver_key(manifest["maximumAppVersion"]): raise ContractError("invalid app compatibility range")
    _require_token(manifest["signingKeyID"],KEY_RE,"signingKeyID")
    _require_int(manifest["runtimeSchemaVersion"],1,2_147_483_647,"runtimeSchemaVersion")
    _require_int(manifest["compressedBytes"],1,None,"compressedBytes")
    _require_int(manifest["installedBytes"],1,None,"installedBytes")
    db=manifest["database"]
    _require_exact(db,{"fileName","byteCount","sha256"},"database")
    if db["fileName"]!="catalog.sqlite3": raise ContractError("invalid database filename")
    _require_int(db["byteCount"],1,MAX_DATABASE_BYTES,"database byteCount"); _require_sha(db["sha256"],"database sha256")
    for label,expected in (("catalogManifest","catalog-manifest.json"),("attribution","ATTRIBUTION.txt")):
        v=manifest[label]; _require_exact(v,{"fileName","sha256"},label)
        if v["fileName"]!=expected: raise ContractError(f"invalid {label} filename")
        _require_sha(v["sha256"],label+" sha256")
    counts=manifest["counts"]
    _require_exact(counts,{"products","uniqueGTINs","ingredientObservations","assessments","retailerEvidence"},"counts")
    for k,v in counts.items(): _require_int(v,0,None,k)
    if counts["products"]!=counts["uniqueGTINs"]: raise ContractError("products and uniqueGTINs must match")
    coverage=manifest["coverage"]
    _require_exact(coverage,{"ingredientCoverageBasisPoints","freshnessState","limitations"},"coverage")
    _require_int(coverage["ingredientCoverageBasisPoints"],0,10000,"ingredient coverage")
    if not isinstance(coverage["freshnessState"],str) or not coverage["freshnessState"] or len(coverage["freshnessState"])>120: raise ContractError("invalid freshnessState")
    limitations=coverage["limitations"]
    if not isinstance(limitations,list) or len(limitations)>32 or any(not isinstance(x,str) or not x or len(x)>500 for x in limitations): raise ContractError("invalid coverage limitations")
    sup=manifest["supersedesModuleID"]
    if sup is not None: _require_token(sup,re.compile(r"^[A-Z]{2}-[A-Za-z0-9._-]{1,120}$"),"supersedesModuleID")
    return manifest

def semver_key(value: str) -> tuple[int,int,int,int,tuple[tuple[int,object],...]]:
    if not VERSION_RE.fullmatch(value): raise ContractError("invalid semantic version")
    without_build=value.split("+",1)[0]
    core,*pre=without_build.split("-",1)
    core_parts=core.split(".")
    if any((len(x)>1 and x.startswith("0")) for x in core_parts): raise ContractError("semantic-version core has leading zero")
    major,minor,patch=map(int,core_parts)
    if not pre:
        return (major,minor,patch,1,())
    identifiers=[]
    for item in pre[0].split("."):
        if not item: raise ContractError("invalid semantic-version prerelease")
        if item.isdigit():
            if len(item)>1 and item.startswith("0"): raise ContractError("numeric prerelease identifier has leading zero")
            identifiers.append((0,int(item)))
        else:
            identifiers.append((1,item))
    return (major,minor,patch,0,tuple(identifiers))

def assemble_manifest(*, database:pathlib.Path, catalog_manifest:pathlib.Path, attribution:pathlib.Path, market:str, minimum_app_version:str, maximum_app_version:str, signing_key_id:str, release_identity:str, module_id:str, source_snapshot_identity:str, published_at:str, compressed_bytes:int|None=None, supersedes_module_id:str|None=None, limitations:list[str]|None=None)->dict[str,Any]:
    require_regular_file(database,maximum=MAX_DATABASE_BYTES,label="catalog.sqlite3")
    require_regular_file(catalog_manifest,maximum=1024*1024,label="catalog-manifest.json")
    require_regular_file(attribution,maximum=1024*1024,label="ATTRIBUTION.txt")
    inner=load_json(catalog_manifest)
    if inner.get("sha256") != sha256_file(database):
        raise ContractError("inner catalog manifest database digest does not match catalog.sqlite3")
    if not isinstance(inner.get("catalogVersion"), str) or not VERSION_RE.fullmatch(inner["catalogVersion"]):
        raise ContractError("inner catalogVersion is not semantic-version syntax")
    counts=inner.get("counts")
    if not isinstance(counts,dict): raise ContractError("production catalog manifest is missing counts")
    required_counts={"products","ingredientObservations","assessments","retailerEvidence"}
    if not required_counts.issubset(counts): raise ContractError("production catalog manifest is missing module counts")
    products=_require_int(counts["products"],0,None,"inner products")
    ingredients=_require_int(counts["ingredientObservations"],0,None,"inner ingredientObservations")
    assessments=_require_int(counts["assessments"],0,None,"inner assessments")
    retailer=_require_int(counts["retailerEvidence"],0,None,"inner retailerEvidence")
    if inner.get("recordCount") != products: raise ContractError("production catalog recordCount differs from counts.products")
    bps=0 if products==0 else min(10000,(ingredients*10000)//products)
    manifest={
      "schemaVersion":1,"moduleID":module_id,"market":market,
      "catalogVersion":inner["catalogVersion"],"runtimeSchemaVersion":int(inner["schemaVersion"]),"methodologyVersion":inner["methodologyVersion"],
      "minimumAppVersion":minimum_app_version,"maximumAppVersion":maximum_app_version,
      "database":{"fileName":"catalog.sqlite3","byteCount":database.stat().st_size,"sha256":sha256_file(database)},
      "catalogManifest":{"fileName":"catalog-manifest.json","sha256":sha256_file(catalog_manifest)},
      "attribution":{"fileName":"ATTRIBUTION.txt","sha256":sha256_file(attribution)},
      "counts":{"products":products,"uniqueGTINs":products,"ingredientObservations":ingredients,"assessments":assessments,"retailerEvidence":retailer},
      "coverage":{"ingredientCoverageBasisPoints":bps,"freshnessState":"qualified-current-catalog","limitations":limitations or ["Coverage describes the discovered admitted corpus and is not a retailer-completeness claim."]},
      "sourceSnapshotIdentity":source_snapshot_identity,"releaseIdentity":release_identity,
      "compressedBytes":compressed_bytes or database.stat().st_size,"installedBytes":database.stat().st_size+catalog_manifest.stat().st_size+attribution.stat().st_size,
      "publishedAt":published_at,"signingKeyID":signing_key_id,"supersedesModuleID":supersedes_module_id,
    }
    validate_manifest(manifest)
    return manifest

def render_attribution(catalog_manifest: pathlib.Path) -> bytes:
    inner=load_json(catalog_manifest)
    rights=inner.get("rights")
    if not isinstance(rights,dict): raise ContractError("catalog manifest is missing rights metadata")
    licenses=rights.get("licenses")
    attributions=rights.get("attributions")
    if not isinstance(licenses,list) or not licenses or any(not isinstance(x,str) or not x.strip() for x in licenses): raise ContractError("catalog rights licenses are missing")
    if not isinstance(attributions,list) or not attributions or any(not isinstance(x,str) or not x.strip() for x in attributions): raise ContractError("catalog rights attributions are missing")
    lines=["Halal Food EU catalog module data notices","","Licenses:"]
    lines.extend(f"- {x.strip()}" for x in sorted(set(licenses)))
    lines.extend(["","Attributions:"])
    lines.extend(f"- {x.strip()}" for x in sorted(set(attributions)))
    return ("\n".join(lines)+"\n").encode("utf-8")

def verify_private_key_matches_policy(*, policy:dict[str,Any], key_id:str, seed:bytes) -> None:
    policy=validate_trust_policy(policy)
    matches=[k for k in policy["keys"] if k["keyID"]==key_id]
    if len(matches)!=1 or matches[0]["state"]!="active": raise ContractError("signing key is not one active trusted key")
    expected=base64.b64decode(matches[0]["publicKeyBase64"],validate=True)
    if derive_public_key(seed)!=expected: raise ContractError("private signing key does not match the bundled active trust root")

def verify_artifacts(*, database:pathlib.Path, catalog_manifest:pathlib.Path, attribution:pathlib.Path, manifest_data:bytes, signature:bytes, trust_policy:dict[str,Any], release_identity:str) -> dict[str,Any]:
    require_regular_file(database,maximum=MAX_DATABASE_BYTES,label="catalog.sqlite3")
    require_regular_file(catalog_manifest,maximum=1024*1024,label="catalog-manifest.json")
    require_regular_file(attribution,maximum=1024*1024,label="ATTRIBUTION.txt")
    if len(manifest_data) <= 0 or len(manifest_data) > 1024*1024: raise ContractError("module manifest size is outside accepted bounds")
    if len(signature) != 64: raise ContractError("Ed25519 signature must be 64 bytes")
    manifest=verify_release(manifest_data=manifest_data,signature=signature,trust_policy=trust_policy,release_identity=release_identity)
    if database.stat().st_size != manifest["database"]["byteCount"] or sha256_file(database)!=manifest["database"]["sha256"]: raise ContractError("database bytes differ from signed manifest")
    if sha256_file(catalog_manifest)!=manifest["catalogManifest"]["sha256"]: raise ContractError("catalog manifest differs from signed manifest")
    if sha256_file(attribution)!=manifest["attribution"]["sha256"]: raise ContractError("attribution differs from signed manifest")
    inner=load_json(catalog_manifest)
    if inner.get("catalogVersion")!=manifest["catalogVersion"] or inner.get("schemaVersion")!=manifest["runtimeSchemaVersion"] or inner.get("methodologyVersion")!=manifest["methodologyVersion"] or inner.get("sha256")!=manifest["database"]["sha256"]: raise ContractError("inner catalog identity differs from signed module")
    counts=inner.get("counts")
    if not isinstance(counts,dict): raise ContractError("inner catalog counts missing")
    expected={"products":"products","ingredientObservations":"product_observations","assessments":"product_assessments","retailerEvidence":"retailer_evidence"}
    for key,table in expected.items():
        if counts.get(key)!=manifest["counts"][key]: raise ContractError(f"inner catalog {key} count differs from signed module")
    uri=f"file:{database.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri,uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok": raise ContractError("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ContractError("SQLite foreign_key_check failed")
        markets=[row[0] for row in connection.execute("SELECT DISTINCT market FROM products ORDER BY market")]
        if markets != [manifest["market"]]: raise ContractError("SQLite market scope differs from signed module")
        for key,table in expected.items():
            if connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] != manifest["counts"][key]: raise ContractError(f"SQLite {table} count differs from signed module")
        if connection.execute("SELECT COUNT(DISTINCT gtin) FROM products").fetchone()[0] != manifest["counts"]["uniqueGTINs"]: raise ContractError("SQLite unique GTIN count differs from signed module")
    return manifest

def _private_der(seed:bytes)->bytes:
    if len(seed)!=32: raise ContractError("Ed25519 private seed must be 32 bytes")
    return PRIVATE_DER_PREFIX+seed

def _public_der(raw:bytes)->bytes:
    if len(raw)!=32: raise ContractError("Ed25519 public key must be 32 bytes")
    return PUBLIC_DER_PREFIX+raw

def _run_openssl(args:list[str], *, stdin:bytes|None=None)->bytes:
    try:
        p=subprocess.run(["openssl",*args],input=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    except subprocess.CalledProcessError as exc:
        raise ContractError("OpenSSL Ed25519 operation failed") from exc
    return p.stdout

def derive_public_key(seed:bytes)->bytes:
    with tempfile.TemporaryDirectory() as td:
        private=pathlib.Path(td)/"private.der"; private.write_bytes(_private_der(seed))
        public=_run_openssl(["pkey","-inform","DER","-in",str(private),"-pubout","-outform","DER"])
        if not public.startswith(PUBLIC_DER_PREFIX) or len(public)!=len(PUBLIC_DER_PREFIX)+32: raise ContractError("unexpected Ed25519 public key encoding")
        return public[len(PUBLIC_DER_PREFIX):]

def sign_bytes(data:bytes, seed:bytes)->bytes:
    with tempfile.TemporaryDirectory() as td:
        private=pathlib.Path(td)/"private.der"; private.write_bytes(_private_der(seed))
        message=pathlib.Path(td)/"message"; message.write_bytes(data)
        sig=_run_openssl(["pkeyutl","-sign","-rawin","-inkey",str(private),"-keyform","DER","-in",str(message)])
        if len(sig)!=64: raise ContractError("Ed25519 signature must be 64 bytes")
        return sig

def verify_bytes(data:bytes, signature:bytes, public_key:bytes)->None:
    if len(signature)!=64: raise ContractError("Ed25519 signature must be 64 bytes")
    with tempfile.TemporaryDirectory() as td:
        pub=pathlib.Path(td)/"public.der"; pub.write_bytes(_public_der(public_key))
        message=pathlib.Path(td)/"message"; message.write_bytes(data)
        sig=pathlib.Path(td)/"sig"; sig.write_bytes(signature)
        try: subprocess.run(["openssl","pkeyutl","-verify","-rawin","-pubin","-inkey",str(pub),"-keyform","DER","-in",str(message),"-sigfile",str(sig)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        except subprocess.CalledProcessError as exc: raise ContractError("invalid Ed25519 signature") from exc

def verify_release(*, manifest_data:bytes, signature:bytes, trust_policy:dict[str,Any], release_identity:str, allow_retired:bool=False)->dict[str,Any]:
    manifest=validate_manifest(json.loads(manifest_data))
    if canonical_json(manifest)!=manifest_data: raise ContractError("module manifest is not canonical JSON")
    policy=validate_trust_policy(trust_policy)
    if manifest["releaseIdentity"]!=release_identity: raise ContractError("release identity mismatch")
    if manifest["moduleID"] in policy["revokedModuleIDs"] or manifest["database"]["sha256"] in policy["revokedDatabaseSha256"]: raise ContractError("module is revoked")
    matches=[k for k in policy["keys"] if k["keyID"]==manifest["signingKeyID"]]
    if len(matches)!=1: raise ContractError("signing key is not trusted")
    key=matches[0]
    if key["state"]=="revoked" or (key["state"]=="retired" and not allow_retired): raise ContractError("signing key is not active")
    verify_bytes(manifest_data,signature,base64.b64decode(key["publicKeyBase64"],validate=True))
    return manifest

def _private_seed_from_env(name:str)->bytes:
    value=os.environ.get(name)
    if not value: raise ContractError(f"missing private key environment variable {name}")
    try: seed=base64.b64decode(value,validate=True)
    except Exception as exc: raise ContractError("invalid private key base64") from exc
    if len(seed)!=32: raise ContractError("Ed25519 private seed must be 32 bytes")
    return seed

def main()->int:
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="command",required=True)
    v=sub.add_parser("validate-trust"); v.add_argument("--policy",type=pathlib.Path,required=True)
    a=sub.add_parser("assemble");
    for name in ("database","catalog-manifest","attribution"): a.add_argument("--"+name,type=pathlib.Path,required=True)
    for name in ("market","minimum-app-version","maximum-app-version","signing-key-id","release-identity","module-id","source-snapshot-identity","published-at"): a.add_argument("--"+name,required=True)
    a.add_argument("--supersedes-module-id")
    a.add_argument("--compressed-bytes", type=int)
    a.add_argument("--limitation", action="append", dest="limitations")
    a.add_argument("--output",type=pathlib.Path,required=True)
    at=sub.add_parser("write-attribution"); at.add_argument("--catalog-manifest",type=pathlib.Path,required=True); at.add_argument("--output",type=pathlib.Path,required=True)
    k=sub.add_parser("verify-key"); k.add_argument("--policy",type=pathlib.Path,required=True); k.add_argument("--key-id",required=True); k.add_argument("--private-key-env",default="CATALOG_SIGNING_PRIVATE_KEY")
    s=sub.add_parser("sign"); s.add_argument("--manifest",type=pathlib.Path,required=True); s.add_argument("--private-key-env",default="CATALOG_SIGNING_PRIVATE_KEY"); s.add_argument("--output",type=pathlib.Path,required=True)
    vr=sub.add_parser("verify"); vr.add_argument("--manifest",type=pathlib.Path,required=True); vr.add_argument("--signature",type=pathlib.Path,required=True); vr.add_argument("--policy",type=pathlib.Path,required=True); vr.add_argument("--release-identity",required=True); vr.add_argument("--allow-retired",action="store_true")
    va=sub.add_parser("verify-artifacts")
    for name in ("database","catalog-manifest","attribution","manifest","signature","policy"): va.add_argument("--"+name,type=pathlib.Path,required=True)
    va.add_argument("--release-identity",required=True)
    args=p.parse_args()
    try:
        if args.command=="validate-trust": validate_trust_policy(load_json(args.policy))
        elif args.command=="write-attribution": args.output.write_bytes(render_attribution(args.catalog_manifest))
        elif args.command=="verify-key": verify_private_key_matches_policy(policy=load_json(args.policy),key_id=args.key_id,seed=_private_seed_from_env(args.private_key_env))
        elif args.command=="assemble":
            m=assemble_manifest(database=args.database,catalog_manifest=args.catalog_manifest,attribution=args.attribution,market=args.market,minimum_app_version=args.minimum_app_version,maximum_app_version=args.maximum_app_version,signing_key_id=args.signing_key_id,release_identity=args.release_identity,module_id=args.module_id,source_snapshot_identity=args.source_snapshot_identity,published_at=args.published_at,compressed_bytes=args.compressed_bytes,supersedes_module_id=args.supersedes_module_id,limitations=args.limitations)
            args.output.write_bytes(canonical_json(m))
        elif args.command=="sign": args.output.write_bytes(sign_bytes(args.manifest.read_bytes(),_private_seed_from_env(args.private_key_env)))
        elif args.command=="verify": verify_release(manifest_data=args.manifest.read_bytes(),signature=args.signature.read_bytes(),trust_policy=load_json(args.policy),release_identity=args.release_identity,allow_retired=args.allow_retired)
        elif args.command=="verify-artifacts": verify_artifacts(database=args.database,catalog_manifest=args.catalog_manifest,attribution=args.attribution,manifest_data=args.manifest.read_bytes(),signature=args.signature.read_bytes(),trust_policy=load_json(args.policy),release_identity=args.release_identity)
    except (ContractError,KeyError,json.JSONDecodeError,OSError) as exc:
        p.error(str(exc))
    return 0
if __name__=="__main__": raise SystemExit(main())
