import base64, json, os, pathlib, sqlite3, tempfile, unittest
from Tools import catalog_module_release as mod

SEED=bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
PUBLIC=bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")

class Tests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory(); self.root=pathlib.Path(self.t.name)
        self.db=self.root/"catalog.sqlite3"; self.db.write_bytes(b"sqlite")
        self.inner=self.root/"catalog-manifest.json"
        self.inner.write_text(json.dumps({"catalogVersion":"1.2.3","schemaVersion":2,"methodologyVersion":"m1","sha256":mod.sha256_file(self.db),"recordCount":2,"counts":{"products":2,"ingredientObservations":1,"assessments":2,"retailerEvidence":1}}))
        self.attr=self.root/"ATTRIBUTION.txt"; self.attr.write_text("license")
        self.policy={"schemaVersion":1,"keys":[{"keyID":"test","publicKeyBase64":base64.b64encode(PUBLIC).decode(),"state":"active"}],"revokedModuleIDs":[],"revokedDatabaseSha256":[]}
    def tearDown(self): self.t.cleanup()
    def manifest(self):
        return mod.assemble_manifest(database=self.db,catalog_manifest=self.inner,attribution=self.attr,market="DE",minimum_app_version="0.1.0",maximum_app_version="1.9.9",signing_key_id="test",release_identity="catalog-de-1.2.3",module_id="DE-1.2.3",source_snapshot_identity="snapshot",published_at="2026-09-08T00:00:00Z")
    def test_rfc8032_public_key(self): self.assertEqual(mod.derive_public_key(SEED),PUBLIC)
    def test_assemble_derives_catalog_identity_and_counts(self):
        m=self.manifest(); self.assertEqual(m["catalogVersion"],"1.2.3"); self.assertEqual(m["counts"]["ingredientObservations"],1); self.assertEqual(m["counts"]["uniqueGTINs"],2)
    def test_sign_verify_and_tamper(self):
        data=mod.canonical_json(self.manifest()); sig=mod.sign_bytes(data,SEED); self.assertEqual(len(sig),64); mod.verify_release(manifest_data=data,signature=sig,trust_policy=self.policy,release_identity="catalog-de-1.2.3")
        with self.assertRaises(mod.ContractError): mod.verify_release(manifest_data=data+b" ",signature=sig,trust_policy=self.policy,release_identity="catalog-de-1.2.3")
    def test_revoked_and_retired_rules(self):
        data=mod.canonical_json(self.manifest()); sig=mod.sign_bytes(data,SEED)
        p=json.loads(json.dumps(self.policy)); p["keys"][0]["state"]="retired"
        with self.assertRaises(mod.ContractError): mod.verify_release(manifest_data=data,signature=sig,trust_policy=p,release_identity="catalog-de-1.2.3")
        mod.verify_release(manifest_data=data,signature=sig,trust_policy=p,release_identity="catalog-de-1.2.3",allow_retired=True)
        p["keys"][0]["state"]="revoked"
        with self.assertRaises(mod.ContractError): mod.verify_release(manifest_data=data,signature=sig,trust_policy=p,release_identity="catalog-de-1.2.3",allow_retired=True)
    def test_payload_digest_binding(self):
        m=self.manifest(); self.assertEqual(m["database"]["sha256"],mod.sha256_file(self.db)); self.db.write_bytes(b"other"); self.assertNotEqual(m["database"]["sha256"],mod.sha256_file(self.db))
        with self.assertRaises(mod.ContractError): self.manifest()
    def test_attribution_is_deterministic_and_requires_rights(self):
        inner=json.loads(self.inner.read_text())
        inner["rights"]={"licenses":["ODbL","Database Contents License","ODbL"],"attributions":["B","A","A"]}
        self.inner.write_text(json.dumps(inner))
        text=mod.render_attribution(self.inner).decode()
        self.assertEqual(text,"Halal Food EU catalog module data notices\n\nLicenses:\n- Database Contents License\n- ODbL\n\nAttributions:\n- A\n- B\n")

    def test_private_key_must_match_active_bundled_trust_root(self):
        mod.verify_private_key_matches_policy(policy=self.policy,key_id="test",seed=SEED)
        wrong=json.loads(json.dumps(self.policy)); wrong["keys"][0]["state"]="retired"
        with self.assertRaises(mod.ContractError): mod.verify_private_key_matches_policy(policy=wrong,key_id="test",seed=SEED)

    def test_verify_artifacts_rechecks_sqlite_market_counts_and_digests(self):
        self.db.unlink()
        connection=sqlite3.connect(self.db)
        connection.executescript("""
        CREATE TABLE products(gtin TEXT PRIMARY KEY, market TEXT NOT NULL);
        CREATE TABLE product_observations(id INTEGER PRIMARY KEY);
        CREATE TABLE product_assessments(id INTEGER PRIMARY KEY);
        CREATE TABLE retailer_evidence(id INTEGER PRIMARY KEY);
        INSERT INTO products(gtin,market) VALUES ('00200000000004','DE'),('00200000000028','DE');
        INSERT INTO product_observations(id) VALUES (1);
        INSERT INTO product_assessments(id) VALUES (1),(2);
        INSERT INTO retailer_evidence(id) VALUES (1);
        """)
        connection.close()
        inner=json.loads(self.inner.read_text())
        inner["sha256"]=mod.sha256_file(self.db)
        inner["recordCount"]=2
        inner["counts"]={"products":2,"ingredientObservations":1,"assessments":2,"retailerEvidence":1}
        self.inner.write_text(json.dumps(inner))
        data=mod.canonical_json(self.manifest()); sig=mod.sign_bytes(data,SEED)
        mod.verify_artifacts(database=self.db,catalog_manifest=self.inner,attribution=self.attr,manifest_data=data,signature=sig,trust_policy=self.policy,release_identity="catalog-de-1.2.3")
        connection=sqlite3.connect(self.db); connection.execute("UPDATE products SET market='FR' WHERE gtin='00200000000028'"); connection.commit(); connection.close()
        with self.assertRaises(mod.ContractError): mod.verify_artifacts(database=self.db,catalog_manifest=self.inner,attribution=self.attr,manifest_data=data,signature=sig,trust_policy=self.policy,release_identity="catalog-de-1.2.3")

    def test_semver_and_published_at_are_strict(self):
        manifest=self.manifest(); manifest["minimumAppVersion"]="2.0.0"; manifest["maximumAppVersion"]="1.0.0"
        with self.assertRaises(mod.ContractError): mod.validate_manifest(manifest)
        manifest=self.manifest(); manifest["publishedAt"]="not-a-date"
        with self.assertRaises(mod.ContractError): mod.validate_manifest(manifest)
        self.assertLess(mod.semver_key("1.0.0-alpha.1"),mod.semver_key("1.0.0"))
        self.assertEqual(mod.semver_key("1.0.0+build.1")[:4],mod.semver_key("1.0.0+build.2")[:4])
    def test_noncanonical_manifest_rejected(self):
        raw=json.dumps(self.manifest(),indent=2).encode(); sig=mod.sign_bytes(raw,SEED)
        with self.assertRaises(mod.ContractError): mod.verify_release(manifest_data=raw,signature=sig,trust_policy=self.policy,release_identity="catalog-de-1.2.3")

if __name__ == '__main__': unittest.main()
