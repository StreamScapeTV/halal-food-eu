import CryptoKit
import Foundation
import SQLite3
import Testing
@testable import HalalFoodEU

@Suite("Signed catalog module manager")
struct CatalogModuleManagerTests {
    @Test("A valid signed production-shaped module installs atomically and becomes queryable")
    func installsVerifiedModule() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let manager = fixture.makeManager()

        let installed = try await manager.installLatest(for: .germany)
        #expect(installed.manifest.moduleID == fixture.moduleID)
        #expect(installed.manifest.database.sha256 == fixture.databaseDigest)
        #expect(FileManager.default.fileExists(atPath: installed.databaseURL.path))

        let resolved = try await fixture.router.resolveProduct(
            for: try Barcode(validating: "0200000000004")
        )
        #expect(resolved.market == .germany)
        #expect(resolved.catalogVersion == fixture.catalogVersion)
        #expect(resolved.product?.name == "Demonstration Oat Drink")

        let recovered = try await manager.installedModules()
        #expect(recovered.map(\.manifest.moduleID) == [fixture.moduleID])
    }

    @Test("Tampered signed manifest bytes are rejected before installation")
    func rejectsTamperedManifest() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        var candidate = fixture.candidate
        var bytes = candidate.manifestData
        bytes[bytes.startIndex] ^= 0x01
        candidate = CatalogModuleCandidate(
            releaseIdentity: candidate.releaseIdentity,
            manifestData: bytes,
            signatureData: candidate.signatureData,
            databaseURL: candidate.databaseURL,
            catalogManifestURL: candidate.catalogManifestURL,
            attributionURL: candidate.attributionURL
        )
        let manager = fixture.makeManager(candidate: candidate)

        do {
            _ = try await manager.installLatest(for: .germany)
            Issue.record("Tampered module manifest must fail closed")
        } catch CatalogModuleError.invalidManifest, CatalogModuleError.invalidSignature {
            // Expected: malformed/canonical/signature validation may reject first.
        } catch {
            Issue.record("Expected signed-manifest rejection, got \(error)")
        }
    }

    @Test("A persisted module is re-verified and remains usable with a retired key")
    func retiredKeyAllowsPersistedActivation() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let manager = fixture.makeManager()
        _ = try await manager.installLatest(for: .germany)

        try fixture.writeTrustPolicy(keyState: .retired)
        let recoveredRouter = fixture.makeBundledRouter()
        let recoveredManager = fixture.makeManager(router: recoveredRouter)
        try await recoveredManager.activatePersistedSelection(.germany)

        let modules = try await recoveredManager.installedModules()
        #expect(modules.map(\.manifest.moduleID) == [fixture.moduleID])
        let resolved = try await recoveredRouter.resolveProduct(
            for: try Barcode(validating: "0200000000004")
        )
        #expect(resolved.catalogVersion == fixture.catalogVersion)
    }

    @Test("A revoked persisted module fails closed and Germany falls back to the bundle")
    func revokedModuleFallsBackToBundledGermany() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let manager = fixture.makeManager()
        _ = try await manager.installLatest(for: .germany)

        try fixture.writeTrustPolicy(
            keyState: .active,
            revokedModuleIDs: [fixture.moduleID]
        )
        let recoveredRouter = fixture.makeBundledRouter()
        let recoveredManager = fixture.makeManager(router: recoveredRouter)
        try await recoveredManager.activatePersistedSelection(.germany)

        #expect(try await recoveredManager.installedModules().isEmpty)
        #expect(try await recoveredRouter.catalogVersion(for: .germany) == fixture.bundledCatalogVersion)
    }

    @Test("A tampered persisted database is re-verified and never registered")
    func tamperedPersistedDatabaseFallsBack() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let manager = fixture.makeManager()
        let installed = try await manager.installLatest(for: .germany)

        var bytes = try Data(contentsOf: installed.databaseURL)
        bytes[bytes.startIndex] ^= 0x01
        try bytes.write(to: installed.databaseURL, options: .atomic)

        let recoveredRouter = fixture.makeBundledRouter()
        let recoveredManager = fixture.makeManager(router: recoveredRouter)
        try await recoveredManager.activatePersistedSelection(.germany)

        #expect(try await recoveredManager.installedModules().isEmpty)
        #expect(try await recoveredRouter.catalogVersion(for: .germany) == fixture.bundledCatalogVersion)
    }

    @Test("A truncated detached signature is rejected before installation")
    func rejectsTruncatedSignature() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let signature = fixture.candidate.signatureData.dropLast()
        let candidate = CatalogModuleCandidate(
            releaseIdentity: fixture.candidate.releaseIdentity,
            manifestData: fixture.candidate.manifestData,
            signatureData: Data(signature),
            databaseURL: fixture.candidate.databaseURL,
            catalogManifestURL: fixture.candidate.catalogManifestURL,
            attributionURL: fixture.candidate.attributionURL
        )

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("A truncated Ed25519 signature must fail closed")
        } catch CatalogModuleError.invalidSignature {
            // Expected.
        } catch {
            Issue.record("Expected invalidSignature, got \(error)")
        }
    }

    @Test("A validly signed module for the wrong market is rejected")
    func rejectsWrongMarket() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let candidate = try fixture.resignedCandidate { manifest in
            manifest["market"] = "FR"
        }

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("A signed module for another market must never activate")
        } catch CatalogModuleError.wrongMarket(let expected, let actual) {
            #expect(expected == "DE")
            #expect(actual == "FR")
        } catch {
            Issue.record("Expected wrongMarket, got \(error)")
        }
    }

    @Test("Signed metadata exceeding module byte bounds is rejected before file activation")
    func rejectsOversizedManifestBounds() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let candidate = try fixture.resignedCandidate { manifest in
            var database = manifest["database"] as! [String: Any]
            database["byteCount"] = CatalogModuleManager.maximumDatabaseBytes + 1
            manifest["database"] = database
        }

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("Oversized signed metadata must fail before activation")
        } catch CatalogModuleError.invalidManifest {
            // Expected.
        } catch {
            Issue.record("Expected invalidManifest, got \(error)")
        }
    }

    @Test("A signed module with an incompatible runtime schema is rejected")
    func rejectsIncompatibleRuntimeSchema() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let candidate = try fixture.resignedCandidate { manifest in
            manifest["runtimeSchemaVersion"] = 999
        }

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("An incompatible signed runtime schema must not activate")
        } catch CatalogModuleError.invalidCatalog, CatalogModuleError.invalidManifest {
            // Expected: envelope/inner-catalog schema binding fails closed before activation.
        } catch {
            Issue.record("Expected incompatible schema rejection, got \(error)")
        }
    }

    @Test("A signed module outside the running app compatibility range is rejected")
    func rejectsIncompatibleAppRange() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let candidate = try fixture.resignedCandidate { manifest in
            manifest["minimumAppVersion"] = "2.0.0"
            manifest["maximumAppVersion"] = "3.0.0"
        }

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("An incompatible signed module must not activate")
        } catch CatalogModuleError.incompatibleApp(let minimum, let maximum, let actual) {
            #expect(minimum == "2.0.0")
            #expect(maximum == "3.0.0")
            #expect(actual == "1.0.0")
        } catch {
            Issue.record("Expected incompatibleApp, got \(error)")
        }
    }

    @Test("A symlinked catalog database is rejected even when it points at valid bytes")
    func rejectsSymlinkedDatabase() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let link = fixture.candidateRoot.appendingPathComponent("symlinked-catalog.sqlite3")
        try FileManager.default.createSymbolicLink(
            at: link,
            withDestinationURL: fixture.candidate.databaseURL
        )
        let candidate = CatalogModuleCandidate(
            releaseIdentity: fixture.candidate.releaseIdentity,
            manifestData: fixture.candidate.manifestData,
            signatureData: fixture.candidate.signatureData,
            databaseURL: link,
            catalogManifestURL: fixture.candidate.catalogManifestURL,
            attributionURL: fixture.candidate.attributionURL
        )

        do {
            _ = try await fixture.makeManager(candidate: candidate).installLatest(for: .germany)
            Issue.record("Symlinked catalog assets must fail closed")
        } catch CatalogModuleError.invalidManifest {
            // Expected file-type rejection.
        } catch {
            Issue.record("Expected invalidManifest, got \(error)")
        }
    }

    @Test("A crash after version promotion but before pointer write recovers the verified module")
    func recoversPromotedModuleWithoutPointer() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        try fixture.materializePromotedModuleWithoutPointer()

        let installed = try await fixture.makeManager().installLatest(for: .germany)
        #expect(installed.manifest.moduleID == fixture.moduleID)
        #expect(
            FileManager.default.fileExists(
                atPath: fixture.installRoot
                    .appendingPathComponent("DE", isDirectory: true)
                    .appendingPathComponent(CatalogModuleManager.activePointerFileName)
                    .path
            )
        )
    }

    @Test("A GitHub outage leaves the bundled catalog queryable")
    func outageLeavesBundledFallbackUsable() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let router = fixture.makeBundledRouter()
        let manager = CatalogModuleManager(
            rootDirectory: fixture.installRoot,
            trustPolicyURL: fixture.trustPolicyURL,
            appVersion: "1.0.0",
            router: router,
            transport: FailingModuleTransport()
        )

        do {
            _ = try await manager.installLatest(for: .germany)
            Issue.record("A simulated GitHub outage must fail the optional update")
        } catch CatalogModuleError.downloadFailed {
            // Expected.
        } catch {
            Issue.record("Expected downloadFailed, got \(error)")
        }

        let resolved = try await router.resolveProduct(
            for: try Barcode(validating: "0200000000004")
        )
        #expect(resolved.market == .germany)
        #expect(resolved.product?.name == "Demonstration Oat Drink")
    }

    @Test("A corrupt replacement rolls back to the previous verified module")
    func corruptReplacementRollsBackToPreviousVerifiedModule() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let replacement = try fixture.makeReplacementCandidate()

        let original = try await fixture.makeManager().installLatest(for: .germany)
        #expect(original.manifest.moduleID == fixture.moduleID)

        let replacementManager = fixture.makeManager(candidate: replacement.candidate)
        let updated = try await replacementManager.installLatest(for: .germany)
        #expect(updated.manifest.moduleID == replacement.moduleID)
        #expect(updated.manifest.catalogVersion == replacement.catalogVersion)
        #expect(try await fixture.router.catalogVersion(for: .germany) == replacement.catalogVersion)

        var bytes = try Data(contentsOf: updated.databaseURL)
        bytes[bytes.startIndex] ^= 0x01
        try bytes.write(to: updated.databaseURL, options: .atomic)

        let recoveredRouter = fixture.makeBundledRouter()
        let recoveredManager = fixture.makeManager(router: recoveredRouter)
        try await recoveredManager.activatePersistedSelection(.germany)

        let recovered = try await recoveredManager.installedModules()
        #expect(recovered.map(\.manifest.moduleID) == [fixture.moduleID])
        #expect(try await recoveredRouter.catalogVersion(for: .germany) == fixture.catalogVersion)
    }

    @Test("Cancelling an update leaves no partial module and preserves the bundled catalog")
    func cancelledUpdateLeavesBundledFallbackUsable() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let router = fixture.makeBundledRouter()
        let manager = CatalogModuleManager(
            rootDirectory: fixture.installRoot,
            trustPolicyURL: fixture.trustPolicyURL,
            appVersion: "1.0.0",
            router: router,
            transport: SlowModuleTransport(candidate: fixture.candidate)
        )

        let task = Task {
            try await manager.installLatest(for: .germany)
        }
        await Task.yield()
        task.cancel()

        do {
            _ = try await task.value
            Issue.record("A cancelled optional update must not complete installation")
        } catch is CancellationError {
            // Expected.
        } catch {
            Issue.record("Expected CancellationError, got \(error)")
        }

        #expect(try await manager.installedModules().isEmpty)
        let resolved = try await router.resolveProduct(
            for: try Barcode(validating: "0200000000004")
        )
        #expect(resolved.market == .germany)
        #expect(resolved.product?.name == "Demonstration Oat Drink")
    }

    @Test("New downloads are disabled when no active trust root is provisioned")
    func emptyTrustPolicyDisablesUpdates() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        try fixture.writeEmptyTrustPolicy()
        let manager = fixture.makeManager()

        do {
            _ = try await manager.availableRemoteMarkets()
            Issue.record("An app build without an active trust root must not offer updates")
        } catch CatalogModuleError.updatesUnavailable {
            // Expected.
        } catch {
            Issue.record("Expected updatesUnavailable, got \(error)")
        }
    }

    @Test("Removing an active non-Germany download leaves that market selected and unavailable")
    func removingActiveNonGermanyModuleDoesNotFallBack() async throws {
        let fixture = try ModuleFixture()
        defer { fixture.cleanup() }
        let france = try CatalogMarket(validating: "FR")
        let catalog = SQLiteProductCatalog(
            databaseURL: fixture.sourceDatabaseURL,
            manifestURL: fixture.sourceManifestURL,
            expectedMarket: .germany
        )
        let search = SQLiteProductSearchCatalog(
            databaseURL: fixture.sourceDatabaseURL,
            manifestURL: fixture.sourceManifestURL
        )
        await fixture.router.registerDownloadedModule(
            market: france,
            catalog: catalog,
            searchCatalog: search,
            catalogVersion: fixture.catalogVersion
        )
        await fixture.router.selectMarket(france)
        let manager = fixture.makeManager()

        try await manager.removeDownloadedModule(for: france)

        #expect(await fixture.router.activeMarket() == france)
        do {
            _ = try await fixture.router.resolveProduct(
                for: try Barcode(validating: "0200000000004")
            )
            Issue.record("Expected the removed active market to remain explicitly unavailable")
        } catch ProductCatalogError.unavailable(let message) {
            #expect(message.contains("FR"))
        }
    }

    @Test("CryptoKit verifies the RFC 8032 Ed25519 test vector used by release tooling")
    func cryptoKitMatchesRFC8032() throws {
        let publicKey = try Curve25519.Signing.PublicKey(
            rawRepresentation: try Data.strictHex(
                "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
            )
        )
        let signature = try Data.strictHex(
            "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
                + "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
        )
        #expect(publicKey.isValidSignature(signature, for: Data()))
    }
}

private extension Data {
    static func strictHex(_ value: String) throws -> Data {
        guard value.count.isMultiple(of: 2), value.allSatisfy({ $0.isHexDigit }) else {
            throw CocoaError(.coderInvalidValue)
        }
        var result = Data(capacity: value.count / 2)
        var index = value.startIndex
        while index < value.endIndex {
            let next = value.index(index, offsetBy: 2)
            guard let byte = UInt8(value[index..<next], radix: 16) else {
                throw CocoaError(.coderInvalidValue)
            }
            result.append(byte)
            index = next
        }
        return result
    }
}

private final class ModuleFixture: @unchecked Sendable {
    let root: URL
    let installRoot: URL
    let candidateRoot: URL
    let trustPolicyURL: URL
    let sourceDatabaseURL: URL
    let sourceManifestURL: URL
    let privateKey: Curve25519.Signing.PrivateKey
    let moduleID: String
    let releaseIdentity: String
    let catalogVersion: String
    let bundledCatalogVersion: String
    let databaseDigest: String
    let candidate: CatalogModuleCandidate
    let router: MarketCatalogRouter

    init() throws {
        root = FileManager.default.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-CatalogModuleTests-\(UUID().uuidString)", isDirectory: true)
        installRoot = root.appendingPathComponent("installed", isDirectory: true)
        candidateRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-CatalogModule-\(UUID().uuidString)", isDirectory: true)
        trustPolicyURL = root.appendingPathComponent("trust-policy.json")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: candidateRoot, withIntermediateDirectories: true)

        let bundle = Bundle(for: ModuleBundleToken.self)
        sourceDatabaseURL = try #require(
            bundle.url(forResource: "catalog", withExtension: "sqlite3"),
            "catalog.sqlite3 must be copied into the unit-test bundle"
        )
        sourceManifestURL = try #require(
            bundle.url(forResource: "catalog-manifest", withExtension: "json"),
            "catalog-manifest.json must be copied into the unit-test bundle"
        )
        let innerData = try Data(contentsOf: sourceManifestURL)
        let inner = try #require(
            JSONSerialization.jsonObject(with: innerData) as? [String: Any],
            "production catalog manifest must be a JSON object"
        )
        catalogVersion = try #require(inner["catalogVersion"] as? String)
        bundledCatalogVersion = catalogVersion
        let runtimeSchemaVersion = try #require(inner["schemaVersion"] as? Int)
        let methodologyVersion = try #require(inner["methodologyVersion"] as? String)
        let counts = try #require(inner["counts"] as? [String: Any])
        let products = try Self.integer(counts["products"])
        let ingredients = try Self.integer(counts["ingredientObservations"])
        let assessments = try Self.integer(counts["assessments"])
        let retailerEvidence = try Self.integer(counts["retailerEvidence"])

        moduleID = "DE-\(catalogVersion)"
        releaseIdentity = "catalog-de-\(catalogVersion)"
        privateKey = Curve25519.Signing.PrivateKey()
        databaseDigest = try Self.sha256(sourceDatabaseURL)

        let candidateDatabase = candidateRoot.appendingPathComponent(CatalogModuleManager.databaseFileName)
        let candidateCatalogManifest = candidateRoot.appendingPathComponent(CatalogModuleManager.catalogManifestFileName)
        let attribution = candidateRoot.appendingPathComponent(CatalogModuleManager.attributionFileName)
        try FileManager.default.copyItem(at: sourceDatabaseURL, to: candidateDatabase)
        try FileManager.default.copyItem(at: sourceManifestURL, to: candidateCatalogManifest)
        try Data("Synthetic test attribution; not production source data.\n".utf8).write(to: attribution)

        let manifestObject: [String: Any] = [
            "schemaVersion": 1,
            "moduleID": moduleID,
            "market": "DE",
            "catalogVersion": catalogVersion,
            "runtimeSchemaVersion": runtimeSchemaVersion,
            "methodologyVersion": methodologyVersion,
            "minimumAppVersion": "0.1.0",
            "maximumAppVersion": "99.0.0",
            "database": [
                "fileName": CatalogModuleManager.databaseFileName,
                "byteCount": try Self.fileSize(candidateDatabase),
                "sha256": databaseDigest,
            ],
            "catalogManifest": [
                "fileName": CatalogModuleManager.catalogManifestFileName,
                "sha256": try Self.sha256(candidateCatalogManifest),
            ],
            "attribution": [
                "fileName": CatalogModuleManager.attributionFileName,
                "sha256": try Self.sha256(attribution),
            ],
            "counts": [
                "products": products,
                "uniqueGTINs": products,
                "ingredientObservations": ingredients,
                "assessments": assessments,
                "retailerEvidence": retailerEvidence,
            ],
            "coverage": [
                "ingredientCoverageBasisPoints": products == 0 ? 0 : (ingredients * 10_000) / products,
                "freshnessState": "qualified-current-catalog",
                "limitations": ["Synthetic production-shaped fixture; no completeness claim."],
            ],
            "sourceSnapshotIdentity": "test-fixture",
            "releaseIdentity": releaseIdentity,
            "compressedBytes": try Self.fileSize(candidateDatabase),
            "installedBytes": try Self.fileSize(candidateDatabase) + Self.fileSize(candidateCatalogManifest) + Self.fileSize(attribution),
            "publishedAt": "2026-09-08T00:00:00Z",
            "signingKeyID": "test-ed25519",
            "supersedesModuleID": NSNull(),
        ]
        var manifestData = try JSONSerialization.data(
            withJSONObject: manifestObject,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        manifestData.append(0x0A)
        let signature = try privateKey.signature(for: manifestData)

        candidate = CatalogModuleCandidate(
            releaseIdentity: releaseIdentity,
            manifestData: manifestData,
            signatureData: signature,
            databaseURL: candidateDatabase,
            catalogManifestURL: candidateCatalogManifest,
            attributionURL: attribution
        )

        try writeTrustPolicy(keyState: .active)
        router = try Self.makeRouter(databaseURL: sourceDatabaseURL, manifestURL: sourceManifestURL)
    }

    func cleanup() {
        try? FileManager.default.removeItem(at: root)
        try? FileManager.default.removeItem(at: candidateRoot)
    }

    func makeBundledRouter() -> MarketCatalogRouter {
        // The bundle is immutable for the duration of the test; force-try only wraps fixture construction.
        try! Self.makeRouter(databaseURL: sourceDatabaseURL, manifestURL: sourceManifestURL)
    }

    func makeManager(
        candidate: CatalogModuleCandidate? = nil,
        router: MarketCatalogRouter? = nil
    ) -> CatalogModuleManager {
        CatalogModuleManager(
            rootDirectory: installRoot,
            trustPolicyURL: trustPolicyURL,
            appVersion: "1.0.0",
            router: router ?? self.router,
            transport: FixedModuleTransport(candidate: candidate ?? self.candidate)
        )
    }

    struct ReplacementCandidate {
        let candidate: CatalogModuleCandidate
        let moduleID: String
        let catalogVersion: String
    }

    func makeReplacementCandidate() throws -> ReplacementCandidate {
        let parts = catalogVersion.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 3,
              let major = Int(parts[0]),
              let minor = Int(parts[1]),
              let patch = Int(parts[2]) else {
            throw CocoaError(.coderInvalidValue)
        }
        let nextVersion = "\(major).\(minor).\(patch + 1)"
        let nextModuleID = "DE-\(nextVersion)"
        let nextReleaseIdentity = "catalog-de-\(nextVersion)"
        let directory = root.appendingPathComponent("replacement-candidate", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)

        let databaseURL = directory.appendingPathComponent(CatalogModuleManager.databaseFileName)
        let catalogManifestURL = directory.appendingPathComponent(CatalogModuleManager.catalogManifestFileName)
        let attributionURL = directory.appendingPathComponent(CatalogModuleManager.attributionFileName)
        try FileManager.default.copyItem(at: sourceDatabaseURL, to: databaseURL)
        try Self.updateCatalogVersion(nextVersion, databaseURL: databaseURL)
        let databaseDigest = try Self.sha256(databaseURL)

        var inner = try #require(
            JSONSerialization.jsonObject(with: Data(contentsOf: sourceManifestURL)) as? [String: Any]
        )
        inner["catalogVersion"] = nextVersion
        inner["sha256"] = databaseDigest
        try Self.writeJSON(inner, to: catalogManifestURL)
        try Data("Synthetic replacement attribution; not production source data.\n".utf8).write(to: attributionURL)

        var envelope = try #require(
            JSONSerialization.jsonObject(with: candidate.manifestData) as? [String: Any]
        )
        envelope["moduleID"] = nextModuleID
        envelope["catalogVersion"] = nextVersion
        envelope["releaseIdentity"] = nextReleaseIdentity
        envelope["supersedesModuleID"] = moduleID
        envelope["database"] = [
            "fileName": CatalogModuleManager.databaseFileName,
            "byteCount": try Self.fileSize(databaseURL),
            "sha256": databaseDigest,
        ]
        envelope["catalogManifest"] = [
            "fileName": CatalogModuleManager.catalogManifestFileName,
            "sha256": try Self.sha256(catalogManifestURL),
        ]
        envelope["attribution"] = [
            "fileName": CatalogModuleManager.attributionFileName,
            "sha256": try Self.sha256(attributionURL),
        ]
        envelope["compressedBytes"] = try Self.fileSize(databaseURL)
        envelope["installedBytes"] = try Self.fileSize(databaseURL)
            + Self.fileSize(catalogManifestURL)
            + Self.fileSize(attributionURL)

        var manifestData = try JSONSerialization.data(
            withJSONObject: envelope,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        manifestData.append(0x0A)
        return ReplacementCandidate(
            candidate: CatalogModuleCandidate(
                releaseIdentity: nextReleaseIdentity,
                manifestData: manifestData,
                signatureData: try privateKey.signature(for: manifestData),
                databaseURL: databaseURL,
                catalogManifestURL: catalogManifestURL,
                attributionURL: attributionURL
            ),
            moduleID: nextModuleID,
            catalogVersion: nextVersion
        )
    }

    func resignedCandidate(
        mutate: (inout [String: Any]) throws -> Void
    ) throws -> CatalogModuleCandidate {
        var object = try #require(
            JSONSerialization.jsonObject(with: candidate.manifestData) as? [String: Any]
        )
        try mutate(&object)
        var manifestData = try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        manifestData.append(0x0A)
        return CatalogModuleCandidate(
            releaseIdentity: candidate.releaseIdentity,
            manifestData: manifestData,
            signatureData: try privateKey.signature(for: manifestData),
            databaseURL: candidate.databaseURL,
            catalogManifestURL: candidate.catalogManifestURL,
            attributionURL: candidate.attributionURL
        )
    }

    func materializePromotedModuleWithoutPointer() throws {
        let marketDirectory = installRoot.appendingPathComponent("DE", isDirectory: true)
        let finalDirectory = marketDirectory.appendingPathComponent(moduleID, isDirectory: true)
        try FileManager.default.createDirectory(at: finalDirectory, withIntermediateDirectories: true)
        try candidate.manifestData.write(
            to: finalDirectory.appendingPathComponent(CatalogModuleManager.moduleManifestFileName),
            options: .atomic
        )
        try candidate.signatureData.write(
            to: finalDirectory.appendingPathComponent(CatalogModuleManager.signatureFileName),
            options: .atomic
        )
        try FileManager.default.copyItem(
            at: candidate.databaseURL,
            to: finalDirectory.appendingPathComponent(CatalogModuleManager.databaseFileName)
        )
        try FileManager.default.copyItem(
            at: candidate.catalogManifestURL,
            to: finalDirectory.appendingPathComponent(CatalogModuleManager.catalogManifestFileName)
        )
        try FileManager.default.copyItem(
            at: candidate.attributionURL,
            to: finalDirectory.appendingPathComponent(CatalogModuleManager.attributionFileName)
        )
    }

    func writeEmptyTrustPolicy() throws {
        let object: [String: Any] = [
            "schemaVersion": 1,
            "keys": [],
            "revokedModuleIDs": [],
            "revokedDatabaseSha256": [],
        ]
        try Self.writeJSON(object, to: trustPolicyURL)
    }

    func writeTrustPolicy(
        keyState: CatalogModuleTrustPolicy.Key.State,
        revokedModuleIDs: [String] = [],
        revokedDatabaseSha256: [String] = []
    ) throws {
        let object: [String: Any] = [
            "schemaVersion": 1,
            "keys": [[
                "keyID": "test-ed25519",
                "publicKeyBase64": privateKey.publicKey.rawRepresentation.base64EncodedString(),
                "state": keyState.rawValue,
            ]],
            "revokedModuleIDs": revokedModuleIDs,
            "revokedDatabaseSha256": revokedDatabaseSha256,
        ]
        try Self.writeJSON(object, to: trustPolicyURL)
    }

    private static func updateCatalogVersion(_ version: String, databaseURL: URL) throws {
        var database: OpaquePointer?
        let openResult = sqlite3_open_v2(
            databaseURL.path,
            &database,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_FULLMUTEX,
            nil
        )
        guard openResult == SQLITE_OK, let database else {
            if let database { sqlite3_close(database) }
            throw CocoaError(.fileReadUnknown)
        }
        defer { sqlite3_close(database) }

        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(
            database,
            "UPDATE catalog_metadata SET value = ?1 WHERE key = 'catalogVersion';",
            -1,
            &statement,
            nil
        ) == SQLITE_OK, let statement else {
            throw CocoaError(.coderInvalidValue)
        }
        defer { sqlite3_finalize(statement) }
        guard version.withCString { pointer in
                  sqlite3_bind_text(
                      statement,
                      1,
                      pointer,
                      -1,
                      unsafeBitCast(-1, to: sqlite3_destructor_type.self)
                  )
              } == SQLITE_OK,
              sqlite3_step(statement) == SQLITE_DONE,
              sqlite3_changes(database) == 1 else {
            throw CocoaError(.coderInvalidValue)
        }
    }

    private static func makeRouter(databaseURL: URL, manifestURL: URL) throws -> MarketCatalogRouter {
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: manifestURL)) as? [String: Any]
        let version = try #require(manifest?["catalogVersion"] as? String)
        return MarketCatalogRouter(
            bundledGermany: .init(
                catalog: SQLiteProductCatalog(databaseURL: databaseURL, manifestURL: manifestURL),
                searchCatalog: SQLiteProductSearchCatalog(databaseURL: databaseURL, manifestURL: manifestURL),
                catalogVersion: version
            )
        )
    }

    private static func fileSize(_ url: URL) throws -> Int {
        try #require(url.resourceValues(forKeys: [.fileSizeKey]).fileSize)
    }

    private static func sha256(_ url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private static func integer(_ value: Any?) throws -> Int {
        if let int = value as? Int { return int }
        if let number = value as? NSNumber { return number.intValue }
        throw CocoaError(.coderInvalidValue)
    }

    private static func writeJSON(_ object: [String: Any], to url: URL) throws {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
        try data.write(to: url, options: .atomic)
    }
}

private actor SlowModuleTransport: CatalogModuleTransport {
    let candidate: CatalogModuleCandidate

    func availableMarkets() async throws -> [CatalogMarket] { [.germany] }

    func latestCandidate(for market: CatalogMarket) async throws -> CatalogModuleCandidate {
        try await Task.sleep(for: .seconds(30))
        return candidate
    }
}

private actor FailingModuleTransport: CatalogModuleTransport {
    func availableMarkets() async throws -> [CatalogMarket] {
        throw CatalogModuleError.downloadFailed("simulated GitHub outage")
    }

    func latestCandidate(for market: CatalogMarket) async throws -> CatalogModuleCandidate {
        throw CatalogModuleError.downloadFailed("simulated GitHub outage")
    }
}

private actor FixedModuleTransport: CatalogModuleTransport {
    let candidate: CatalogModuleCandidate

    func availableMarkets() async throws -> [CatalogMarket] { [.germany] }
    func latestCandidate(for market: CatalogMarket) async throws -> CatalogModuleCandidate { candidate }
}

private final class ModuleBundleToken {}
