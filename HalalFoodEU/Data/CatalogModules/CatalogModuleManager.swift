import CryptoKit
import Foundation
import SQLite3

actor CatalogModuleManager: CatalogModuleService {
    private enum VerificationPurpose {
        case newInstall
        case persistedActivation
    }

    private struct SemanticVersion: Comparable, Sendable {
        private enum Identifier: Comparable, Sendable {
            case numeric(Int)
            case text(String)

            static func < (lhs: Identifier, rhs: Identifier) -> Bool {
                switch (lhs, rhs) {
                case let (.numeric(left), .numeric(right)):
                    return left < right
                case (.numeric, .text):
                    return true
                case (.text, .numeric):
                    return false
                case let (.text(left), .text(right)):
                    return left < right
                }
            }
        }

        let major: Int
        let minor: Int
        let patch: Int
        private let prerelease: [Identifier]

        init?(_ value: String) {
            let withoutBuild = value.split(separator: "+", maxSplits: 1, omittingEmptySubsequences: false)
            guard !withoutBuild[0].isEmpty,
                  withoutBuild.count <= 2,
                  withoutBuild.count == 1 || Self.validIdentifiers(String(withoutBuild[1])) else { return nil }

            let releaseAndPrerelease = withoutBuild[0].split(
                separator: "-",
                maxSplits: 1,
                omittingEmptySubsequences: false
            )
            guard !releaseAndPrerelease[0].isEmpty else { return nil }
            let core = releaseAndPrerelease[0].split(separator: ".", omittingEmptySubsequences: false)
            guard core.count == 3,
                  let major = Self.numericCore(core[0]),
                  let minor = Self.numericCore(core[1]),
                  let patch = Self.numericCore(core[2]) else { return nil }

            var prerelease: [Identifier] = []
            if releaseAndPrerelease.count == 2 {
                let raw = String(releaseAndPrerelease[1])
                guard Self.validIdentifiers(raw) else { return nil }
                for part in raw.split(separator: ".", omittingEmptySubsequences: false) {
                    let text = String(part)
                    if text.allSatisfy(\.isNumber) {
                        guard text == "0" || !text.hasPrefix("0"), let value = Int(text) else { return nil }
                        prerelease.append(.numeric(value))
                    } else {
                        prerelease.append(.text(text))
                    }
                }
            }

            self.major = major
            self.minor = minor
            self.patch = patch
            self.prerelease = prerelease
        }

        static func < (lhs: SemanticVersion, rhs: SemanticVersion) -> Bool {
            if lhs.major != rhs.major { return lhs.major < rhs.major }
            if lhs.minor != rhs.minor { return lhs.minor < rhs.minor }
            if lhs.patch != rhs.patch { return lhs.patch < rhs.patch }
            if lhs.prerelease.isEmpty != rhs.prerelease.isEmpty {
                return !lhs.prerelease.isEmpty
            }
            for index in 0..<min(lhs.prerelease.count, rhs.prerelease.count) {
                if lhs.prerelease[index] != rhs.prerelease[index] {
                    return lhs.prerelease[index] < rhs.prerelease[index]
                }
            }
            return lhs.prerelease.count < rhs.prerelease.count
        }

        private static func numericCore(_ value: Substring) -> Int? {
            let text = String(value)
            guard !text.isEmpty, text.allSatisfy(\.isNumber),
                  text == "0" || !text.hasPrefix("0") else { return nil }
            return Int(text)
        }

        private static func validIdentifiers(_ value: String) -> Bool {
            guard !value.isEmpty else { return false }
            return value.split(separator: ".", omittingEmptySubsequences: false).allSatisfy { part in
                !part.isEmpty && part.utf8.allSatisfy { byte in
                    (48...57).contains(byte) || (65...90).contains(byte) || (97...122).contains(byte) || byte == 45
                }
            }
        }
    }

    static let maximumDatabaseBytes = 500 * 1024 * 1024
    static let maximumSmallFileBytes = 1024 * 1024
    static let moduleManifestFileName = "catalog-module-manifest.json"
    static let signatureFileName = "catalog-module-manifest.sig"
    static let databaseFileName = "catalog.sqlite3"
    static let catalogManifestFileName = "catalog-manifest.json"
    static let attributionFileName = "ATTRIBUTION.txt"
    static let activePointerFileName = "active-module-id.txt"

    private struct InnerCatalogManifest: Decodable {
        struct Counts: Decodable {
            let products: Int
            let ingredientObservations: Int
            let assessments: Int
            let retailerEvidence: Int
        }
        let catalogVersion: String
        let schemaVersion: Int
        let methodologyVersion: String
        let sha256: String
        let counts: Counts
    }

    private let rootDirectory: URL
    private let trustPolicyURL: URL
    private let appVersion: String
    private let router: MarketCatalogRouter
    private let transport: (any CatalogModuleTransport)?
    private let fileManager: FileManager
    private let decoder = JSONDecoder()

    init(
        rootDirectory: URL,
        trustPolicyURL: URL,
        appVersion: String,
        router: MarketCatalogRouter,
        transport: (any CatalogModuleTransport)? = nil,
        fileManager: FileManager = .default
    ) {
        self.rootDirectory = rootDirectory
        self.trustPolicyURL = trustPolicyURL
        self.appVersion = appVersion
        self.router = router
        self.transport = transport
        self.fileManager = fileManager
    }

    func activatePersistedSelection(_ market: CatalogMarket) async throws {
        try prepareRoot()
        for module in try installedModules() {
            try await register(module)
        }
        try await selectMarket(market)
    }

    func selectMarket(_ market: CatalogMarket) async throws {
        _ = try await router.catalogVersion(for: market)
        try await router.selectMarket(market)
    }

    func installedModules() async throws -> [InstalledCatalogModule] {
        try prepareRoot()
        let children = try fileManager.contentsOfDirectory(
            at: rootDirectory,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )
        var result: [InstalledCatalogModule] = []
        for url in children.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            let values = try url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values.isDirectory == true, values.isSymbolicLink != true,
                  let market = CatalogMarket(rawValue: url.lastPathComponent) else { continue }
            if let module = try await recoverInstalledModule(for: market) {
                result.append(module)
            }
        }
        return result.sorted { $0.manifest.market < $1.manifest.market }
    }

    func availableRemoteMarkets() async throws -> [CatalogMarket] {
        guard let transport else { throw CatalogModuleError.updatesUnavailable }
        let policy = try loadTrustPolicy()
        guard policy.keys.contains(where: { $0.state == .active }) else {
            throw CatalogModuleError.updatesUnavailable
        }
        return try await transport.availableMarkets()
    }

    func installLatest(for market: CatalogMarket) async throws -> InstalledCatalogModule {
        guard let transport else { throw CatalogModuleError.updatesUnavailable }
        let policy = try loadTrustPolicy()
        guard policy.keys.contains(where: { $0.state == .active }) else {
            throw CatalogModuleError.updatesUnavailable
        }

        let candidate = try await transport.latestCandidate(for: market)
        defer { cleanupDownloadedCandidate(candidate) }
        let manifest = try await verify(
            candidate: candidate,
            expectedMarket: market,
            purpose: .newInstall,
            policy: policy
        )

        let current = try await recoverInstalledModule(for: market)
        if let current {
            if current.manifest.moduleID == manifest.moduleID {
                guard current.manifest.database.sha256 == manifest.database.sha256 else {
                    throw CatalogModuleError.replayedOrOutOfOrder("module identity was reused for different bytes")
                }
                return current
            }
            guard manifest.supersedesModuleID == current.manifest.moduleID else {
                throw CatalogModuleError.replayedOrOutOfOrder("supersedesModuleID does not match the active module")
            }
            guard let nextVersion = SemanticVersion(manifest.catalogVersion),
                  let currentVersion = SemanticVersion(current.manifest.catalogVersion),
                  nextVersion > currentVersion else {
                throw CatalogModuleError.replayedOrOutOfOrder("catalog version does not move forward")
            }
        } else if manifest.supersedesModuleID != nil {
            throw CatalogModuleError.replayedOrOutOfOrder("first install cannot supersede an unknown local module")
        }

        let marketDirectory = rootDirectory.appendingPathComponent(market.rawValue, isDirectory: true)
        try fileManager.createDirectory(at: marketDirectory, withIntermediateDirectories: true)
        try excludeFromBackup(marketDirectory)

        let staging = marketDirectory.appendingPathComponent(".staging-\(UUID().uuidString)", isDirectory: true)
        try fileManager.createDirectory(at: staging, withIntermediateDirectories: true)
        defer { try? fileManager.removeItem(at: staging) }
        try excludeFromBackup(staging)

        let moduleManifestURL = staging.appendingPathComponent(Self.moduleManifestFileName)
        let signatureURL = staging.appendingPathComponent(Self.signatureFileName)
        let databaseURL = staging.appendingPathComponent(Self.databaseFileName)
        let catalogManifestURL = staging.appendingPathComponent(Self.catalogManifestFileName)
        let attributionURL = staging.appendingPathComponent(Self.attributionFileName)

        try candidate.manifestData.write(to: moduleManifestURL, options: [.atomic])
        try candidate.signatureData.write(to: signatureURL, options: [.atomic])
        try copy(candidate.databaseURL, to: databaseURL)
        try copy(candidate.catalogManifestURL, to: catalogManifestURL)
        try copy(candidate.attributionURL, to: attributionURL)

        let finalDirectory = marketDirectory.appendingPathComponent(manifest.moduleID, isDirectory: true)
        if fileManager.fileExists(atPath: finalDirectory.path) {
            throw CatalogModuleError.installFailed("the versioned module directory already exists")
        }
        do {
            try fileManager.moveItem(at: staging, to: finalDirectory)
        } catch {
            throw CatalogModuleError.installFailed("atomic promotion failed")
        }

        let installed = try await verifiedInstalledModule(
            at: finalDirectory,
            expectedMarket: market,
            purpose: .persistedActivation,
            policy: policy
        )
        try writeActiveModuleID(installed.manifest.moduleID, marketDirectory: marketDirectory)
        try await register(installed)
        return installed
    }

    func removeDownloadedModule(for market: CatalogMarket) async throws {
        let marketDirectory = rootDirectory.appendingPathComponent(market.rawValue, isDirectory: true)
        if fileManager.fileExists(atPath: marketDirectory.path) {
            try fileManager.removeItem(at: marketDirectory)
        }
        await router.removeDownloadedModule(for: market)
        if market == .germany {
            try await router.selectMarket(.germany)
        }
    }

    private func recoverInstalledModule(for market: CatalogMarket) async throws -> InstalledCatalogModule? {
        let marketDirectory = rootDirectory.appendingPathComponent(market.rawValue, isDirectory: true)
        guard fileManager.fileExists(atPath: marketDirectory.path) else { return nil }
        let marketValues = try marketDirectory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard marketValues.isDirectory == true, marketValues.isSymbolicLink != true else {
            throw CatalogModuleError.installFailed("market module directory is not a regular directory")
        }
        let policy = try loadTrustPolicy()

        if let activeID = try? readActiveModuleID(marketDirectory: marketDirectory),
           Self.isSafeModuleID(activeID),
           let active = try? await verifiedInstalledModule(
                at: marketDirectory.appendingPathComponent(activeID, isDirectory: true),
                expectedMarket: market,
                purpose: .persistedActivation,
                policy: policy
           ) {
            return active
        }

        let candidates = try fileManager.contentsOfDirectory(
            at: marketDirectory,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        ).filter { url in
            guard Self.isSafeModuleID(url.lastPathComponent),
                  let values = try? url.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]) else { return false }
            return values.isDirectory == true && values.isSymbolicLink != true
        }

        var verified: [InstalledCatalogModule] = []
        for directory in candidates {
            if let module = try? await verifiedInstalledModule(
                at: directory,
                expectedMarket: market,
                purpose: .persistedActivation,
                policy: policy
            ) {
                verified.append(module)
            }
        }
        guard let best = verified.max(by: {
            let lhs = SemanticVersion($0.manifest.catalogVersion) ?? SemanticVersion("0.0.0")!
            let rhs = SemanticVersion($1.manifest.catalogVersion) ?? SemanticVersion("0.0.0")!
            return lhs < rhs
        }) else { return nil }
        try writeActiveModuleID(best.manifest.moduleID, marketDirectory: marketDirectory)
        return best
    }

    private func verifiedInstalledModule(
        at directory: URL,
        expectedMarket: CatalogMarket,
        purpose: VerificationPurpose,
        policy: CatalogModuleTrustPolicy
    ) async throws -> InstalledCatalogModule {
        let installed = try syncVerifiedInstalledModule(
            at: directory,
            expectedMarket: expectedMarket,
            purpose: purpose,
            policy: policy
        )
        // Exercise the real repositories before registering the bytes as queryable.
        let catalog = SQLiteProductCatalog(
            databaseURL: installed.databaseURL,
            manifestURL: installed.catalogManifestURL,
            expectedMarket: expectedMarket
        )
        _ = try await catalog.product(for: try Barcode(validating: "00000000000000"))
        let search = SQLiteProductSearchCatalog(
            databaseURL: installed.databaseURL,
            manifestURL: installed.catalogManifestURL
        )
        _ = try await search.search(query: "00000000", limit: 1, offset: 0)
        return installed
    }

    private func syncVerifiedInstalledModule(
        at directory: URL,
        expectedMarket: CatalogMarket,
        purpose: VerificationPurpose,
        policy: CatalogModuleTrustPolicy
    ) throws -> InstalledCatalogModule {
        let directoryValues = try directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard directoryValues.isDirectory == true, directoryValues.isSymbolicLink != true else {
            throw CatalogModuleError.installFailed("versioned module path is not a regular directory")
        }
        let candidate = CatalogModuleCandidate(
            releaseIdentity: try releaseIdentity(at: directory),
            manifestData: try boundedData(directory.appendingPathComponent(Self.moduleManifestFileName), maximum: Self.maximumSmallFileBytes),
            signatureData: try boundedData(directory.appendingPathComponent(Self.signatureFileName), maximum: 4096),
            databaseURL: directory.appendingPathComponent(Self.databaseFileName),
            catalogManifestURL: directory.appendingPathComponent(Self.catalogManifestFileName),
            attributionURL: directory.appendingPathComponent(Self.attributionFileName)
        )
        let manifest = try verifySynchronously(
            candidate: candidate,
            expectedMarket: expectedMarket,
            purpose: purpose,
            policy: policy
        )
        return InstalledCatalogModule(
            manifest: manifest,
            directoryURL: directory,
            databaseURL: candidate.databaseURL,
            catalogManifestURL: candidate.catalogManifestURL,
            attributionURL: candidate.attributionURL,
            moduleManifestURL: directory.appendingPathComponent(Self.moduleManifestFileName),
            signatureURL: directory.appendingPathComponent(Self.signatureFileName)
        )
    }

    private func verify(
        candidate: CatalogModuleCandidate,
        expectedMarket: CatalogMarket,
        purpose: VerificationPurpose,
        policy: CatalogModuleTrustPolicy
    ) async throws -> CatalogModuleManifest {
        try Task.checkCancellation()
        return try verifySynchronously(candidate: candidate, expectedMarket: expectedMarket, purpose: purpose, policy: policy)
    }

    private func verifySynchronously(
        candidate: CatalogModuleCandidate,
        expectedMarket: CatalogMarket,
        purpose: VerificationPurpose,
        policy: CatalogModuleTrustPolicy
    ) throws -> CatalogModuleManifest {
        try validateCanonicalManifestData(candidate.manifestData)
        let manifest = try decoder.decode(CatalogModuleManifest.self, from: candidate.manifestData)
        try validateManifestShape(manifest, expectedMarket: expectedMarket, releaseIdentity: candidate.releaseIdentity)

        if policy.revokedModuleIDs.contains(manifest.moduleID) || policy.revokedDatabaseSha256.contains(manifest.database.sha256) {
            throw CatalogModuleError.revoked(manifest.moduleID)
        }
        guard let key = policy.keys.first(where: { $0.keyID == manifest.signingKeyID }) else {
            throw CatalogModuleError.untrustedKey(manifest.signingKeyID)
        }
        guard key.state != .revoked else { throw CatalogModuleError.revoked(manifest.signingKeyID) }
        if purpose == .newInstall, key.state != .active {
            throw CatalogModuleError.untrustedKey(manifest.signingKeyID)
        }
        guard let publicKeyData = Data(base64Encoded: key.publicKeyBase64), publicKeyData.count == 32 else {
            throw CatalogModuleError.untrustedKey(manifest.signingKeyID)
        }
        guard candidate.signatureData.count == 64 else { throw CatalogModuleError.invalidSignature }
        let publicKey: Curve25519.Signing.PublicKey
        do { publicKey = try .init(rawRepresentation: publicKeyData) }
        catch { throw CatalogModuleError.untrustedKey(manifest.signingKeyID) }
        guard publicKey.isValidSignature(candidate.signatureData, for: candidate.manifestData) else {
            throw CatalogModuleError.invalidSignature
        }

        guard let actualVersion = SemanticVersion(appVersion),
              let minimumVersion = SemanticVersion(manifest.minimumAppVersion),
              let maximumVersion = SemanticVersion(manifest.maximumAppVersion),
              actualVersion >= minimumVersion,
              actualVersion <= maximumVersion else {
            throw CatalogModuleError.incompatibleApp(
                minimum: manifest.minimumAppVersion,
                maximum: manifest.maximumAppVersion,
                actual: appVersion
            )
        }

        try verifyFile(candidate.databaseURL, byteCount: manifest.database.byteCount, digest: manifest.database.sha256, label: Self.databaseFileName, maximum: Self.maximumDatabaseBytes)
        try verifyFile(candidate.catalogManifestURL, digest: manifest.catalogManifest.sha256, label: Self.catalogManifestFileName, maximum: Self.maximumSmallFileBytes)
        try verifyFile(candidate.attributionURL, digest: manifest.attribution.sha256, label: Self.attributionFileName, maximum: Self.maximumSmallFileBytes)

        let innerData = try boundedData(candidate.catalogManifestURL, maximum: Self.maximumSmallFileBytes)
        let inner = try decoder.decode(InnerCatalogManifest.self, from: innerData)
        guard inner.catalogVersion == manifest.catalogVersion,
              inner.schemaVersion == manifest.runtimeSchemaVersion,
              inner.methodologyVersion == manifest.methodologyVersion,
              inner.sha256 == manifest.database.sha256 else {
            throw CatalogModuleError.invalidCatalog("inner catalog identity does not match the signed module envelope")
        }
        guard inner.counts.products == manifest.counts.products,
              inner.counts.ingredientObservations == manifest.counts.ingredientObservations,
              inner.counts.assessments == manifest.counts.assessments,
              inner.counts.retailerEvidence == manifest.counts.retailerEvidence else {
            throw CatalogModuleError.invalidCatalog("signed module counts do not match the inner catalog manifest")
        }
        try verifySQLite(candidate.databaseURL, expectedMarket: expectedMarket, manifest: manifest)
        return manifest
    }

    private func validateManifestShape(
        _ manifest: CatalogModuleManifest,
        expectedMarket: CatalogMarket,
        releaseIdentity: String
    ) throws {
        guard manifest.schemaVersion == 1 else { throw CatalogModuleError.invalidManifest("unsupported schema") }
        guard manifest.market == expectedMarket else {
            throw CatalogModuleError.wrongMarket(expected: expectedMarket.rawValue, actual: manifest.market.rawValue)
        }
        guard manifest.releaseIdentity == releaseIdentity,
              releaseIdentity == "catalog-\(expectedMarket.rawValue.lowercased())-\(manifest.catalogVersion)" else {
            throw CatalogModuleError.invalidManifest("release identity is not immutable market/version identity")
        }
        guard manifest.moduleID == "\(expectedMarket.rawValue)-\(manifest.catalogVersion)", Self.isSafeModuleID(manifest.moduleID) else {
            throw CatalogModuleError.invalidManifest("module identity is invalid")
        }
        guard manifest.database.fileName == Self.databaseFileName,
              manifest.catalogManifest.fileName == Self.catalogManifestFileName,
              manifest.attribution.fileName == Self.attributionFileName else {
            throw CatalogModuleError.invalidManifest("asset filenames are not canonical")
        }
        guard manifest.database.byteCount > 0, manifest.database.byteCount <= Self.maximumDatabaseBytes,
              manifest.installedBytes >= manifest.database.byteCount,
              manifest.installedBytes <= Self.maximumDatabaseBytes + (3 * Self.maximumSmallFileBytes),
              manifest.compressedBytes > 0,
              manifest.compressedBytes <= Self.maximumDatabaseBytes + (3 * Self.maximumSmallFileBytes),
              manifest.counts.products >= 0,
              manifest.counts.uniqueGTINs >= 0,
              manifest.counts.ingredientObservations >= 0,
              manifest.counts.assessments >= 0,
              manifest.counts.retailerEvidence >= 0,
              manifest.counts.products == manifest.counts.uniqueGTINs,
              (0...10_000).contains(manifest.coverage.ingredientCoverageBasisPoints),
              !manifest.coverage.freshnessState.isEmpty,
              manifest.coverage.freshnessState.utf8.count <= 120,
              manifest.coverage.limitations.count <= 32,
              manifest.coverage.limitations.allSatisfy({ !$0.isEmpty && $0.utf8.count <= 500 }),
              !manifest.methodologyVersion.isEmpty, manifest.methodologyVersion.utf8.count <= 120,
              !manifest.sourceSnapshotIdentity.isEmpty, manifest.sourceSnapshotIdentity.utf8.count <= 200,
              manifest.publishedAt.utf8.count <= 64,
              ISO8601DateFormatter().date(from: manifest.publishedAt) != nil,
              Self.isSHA256(manifest.database.sha256),
              Self.isSHA256(manifest.catalogManifest.sha256),
              Self.isSHA256(manifest.attribution.sha256),
              Self.isSafeKeyID(manifest.signingKeyID) else {
            throw CatalogModuleError.invalidManifest("manifest bounds or digest syntax are invalid")
        }
        guard let catalogVersion = SemanticVersion(manifest.catalogVersion),
              let minimumVersion = SemanticVersion(manifest.minimumAppVersion),
              let maximumVersion = SemanticVersion(manifest.maximumAppVersion),
              minimumVersion <= maximumVersion else {
            throw CatalogModuleError.invalidManifest("catalog/app versions must be valid semantic-version ranges")
        }
        _ = catalogVersion
        if let supersedes = manifest.supersedesModuleID, !Self.isSafeModuleID(supersedes) {
            throw CatalogModuleError.invalidManifest("supersedes identity is invalid")
        }
    }

    private func verifySQLite(_ databaseURL: URL, expectedMarket: CatalogMarket, manifest: CatalogModuleManifest) throws {
        var db: OpaquePointer?
        let result = sqlite3_open_v2(databaseURL.path, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nil)
        guard result == SQLITE_OK, let db else {
            if let db { sqlite3_close(db) }
            throw CatalogModuleError.invalidCatalog("SQLite could not be opened read-only")
        }
        defer { sqlite3_close(db) }
        guard sqlite3_exec(db, "PRAGMA query_only = ON;", nil, nil, nil) == SQLITE_OK else {
            throw CatalogModuleError.invalidCatalog("SQLite query-only mode failed")
        }
        guard try scalarText(db, "PRAGMA integrity_check;") == "ok" else {
            throw CatalogModuleError.invalidCatalog("SQLite integrity_check failed")
        }
        guard try !hasAnyRows(db, "PRAGMA foreign_key_check;") else {
            throw CatalogModuleError.invalidCatalog("SQLite foreign_key_check failed")
        }
        let markets = try textRows(db, "SELECT DISTINCT market FROM products ORDER BY market;")
        guard markets == [expectedMarket.rawValue] else {
            throw CatalogModuleError.invalidCatalog("SQLite market scope is not exact")
        }
        let counts: [(String, Int)] = [
            ("products", manifest.counts.products),
            ("product_observations", manifest.counts.ingredientObservations),
            ("product_assessments", manifest.counts.assessments),
            ("retailer_evidence", manifest.counts.retailerEvidence),
        ]
        for (table, expected) in counts {
            guard try scalarInt(db, "SELECT COUNT(*) FROM \(table);") == expected else {
                throw CatalogModuleError.invalidCatalog("\(table) count differs from signed manifest")
            }
        }
        guard try scalarInt(db, "SELECT COUNT(DISTINCT gtin) FROM products;") == manifest.counts.uniqueGTINs else {
            throw CatalogModuleError.invalidCatalog("unique GTIN count differs from signed manifest")
        }
    }

    private func register(_ module: InstalledCatalogModule) async throws {
        await router.registerDownloadedModule(
            market: module.manifest.market,
            catalog: SQLiteProductCatalog(
                databaseURL: module.databaseURL,
                manifestURL: module.catalogManifestURL,
                expectedMarket: module.manifest.market
            ),
            searchCatalog: SQLiteProductSearchCatalog(
                databaseURL: module.databaseURL,
                manifestURL: module.catalogManifestURL
            ),
            catalogVersion: module.manifest.catalogVersion
        )
    }

    private func loadTrustPolicy() throws -> CatalogModuleTrustPolicy {
        let data = try boundedData(trustPolicyURL, maximum: Self.maximumSmallFileBytes)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw CatalogModuleError.invalidManifest("trust policy must be a JSON object")
        }
        try Self.validateExactTrustPolicyKeys(object)
        let policy = try decoder.decode(CatalogModuleTrustPolicy.self, from: data)
        guard policy.schemaVersion == 1,
              policy.keys.count <= 16,
              Set(policy.keys.map(\.keyID)).count == policy.keys.count,
              policy.keys.allSatisfy({ Self.isSafeKeyID($0.keyID) && Data(base64Encoded: $0.publicKeyBase64)?.count == 32 }),
              policy.revokedModuleIDs.count <= 256,
              Set(policy.revokedModuleIDs).count == policy.revokedModuleIDs.count,
              policy.revokedModuleIDs.allSatisfy(Self.isSafeModuleID),
              policy.revokedDatabaseSha256.count <= 256,
              Set(policy.revokedDatabaseSha256).count == policy.revokedDatabaseSha256.count,
              policy.revokedDatabaseSha256.allSatisfy(Self.isSHA256) else {
            throw CatalogModuleError.invalidManifest("trust policy is invalid")
        }
        return policy
    }

    private func prepareRoot() throws {
        try fileManager.createDirectory(at: rootDirectory, withIntermediateDirectories: true)
        let values = try rootDirectory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
        guard values.isDirectory == true, values.isSymbolicLink != true else {
            throw CatalogModuleError.installFailed("catalog module root is not a regular directory")
        }
        try excludeFromBackup(rootDirectory)
    }

    private func excludeFromBackup(_ url: URL) throws {
        var mutable = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try mutable.setResourceValues(values)
    }

    private func copy(_ source: URL, to destination: URL) throws {
        guard source.standardizedFileURL.path != destination.standardizedFileURL.path else { return }
        try fileManager.copyItem(at: source, to: destination)
    }

    private func readActiveModuleID(marketDirectory: URL) throws -> String {
        let url = marketDirectory.appendingPathComponent(Self.activePointerFileName)
        let data = try boundedData(url, maximum: 256)
        guard let value = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              Self.isSafeModuleID(value) else {
            throw CatalogModuleError.installFailed("active module pointer is invalid")
        }
        return value
    }

    private func writeActiveModuleID(_ moduleID: String, marketDirectory: URL) throws {
        guard Self.isSafeModuleID(moduleID) else { throw CatalogModuleError.installFailed("unsafe module identity") }
        let url = marketDirectory.appendingPathComponent(Self.activePointerFileName)
        try Data((moduleID + "\n").utf8).write(to: url, options: [.atomic])
    }

    private func releaseIdentity(at directory: URL) throws -> String {
        let data = try boundedData(directory.appendingPathComponent(Self.moduleManifestFileName), maximum: Self.maximumSmallFileBytes)
        return try decoder.decode(CatalogModuleManifest.self, from: data).releaseIdentity
    }

    private func boundedData(_ url: URL, maximum: Int) throws -> Data {
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size >= 0, size <= maximum else {
            throw CatalogModuleError.invalidManifest("file type or size exceeds accepted bounds")
        }
        return try Data(contentsOf: url, options: [.mappedIfSafe])
    }

    private func verifyFile(_ url: URL, byteCount: Int? = nil, digest: String, label: String, maximum: Int) throws {
        let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size > 0, size <= maximum else {
            throw CatalogModuleError.invalidManifest("\(label) type or size is invalid")
        }
        if let byteCount, size != byteCount { throw CatalogModuleError.digestMismatch(label) }
        let actual = try sha256(of: url)
        guard actual == digest else { throw CatalogModuleError.digestMismatch(label) }
    }

    private func hasAnyRows(_ db: OpaquePointer, _ sql: String) throws -> Bool {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CatalogModuleError.invalidCatalog("SQLite verification query could not be prepared")
        }
        defer { sqlite3_finalize(statement) }
        let step = sqlite3_step(statement)
        if step == SQLITE_ROW { return true }
        guard step == SQLITE_DONE else {
            throw CatalogModuleError.invalidCatalog("SQLite verification query failed")
        }
        return false
    }

    private func scalarText(_ db: OpaquePointer, _ sql: String) throws -> String {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CatalogModuleError.invalidCatalog("SQLite statement could not be prepared")
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW, let text = sqlite3_column_text(statement, 0) else {
            throw CatalogModuleError.invalidCatalog("SQLite scalar text query failed")
        }
        return String(cString: text)
    }

    private func scalarInt(_ db: OpaquePointer, _ sql: String) throws -> Int {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CatalogModuleError.invalidCatalog("SQLite count query could not be prepared")
        }
        defer { sqlite3_finalize(statement) }
        guard sqlite3_step(statement) == SQLITE_ROW else {
            throw CatalogModuleError.invalidCatalog("SQLite count query failed")
        }
        return Int(sqlite3_column_int64(statement, 0))
    }

    private func textRows(_ db: OpaquePointer, _ sql: String) throws -> [String] {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            throw CatalogModuleError.invalidCatalog("SQLite market query could not be prepared")
        }
        defer { sqlite3_finalize(statement) }
        var rows: [String] = []
        while true {
            switch sqlite3_step(statement) {
            case SQLITE_ROW:
                guard let text = sqlite3_column_text(statement, 0) else {
                    throw CatalogModuleError.invalidCatalog("SQLite market is null")
                }
                rows.append(String(cString: text))
            case SQLITE_DONE:
                return rows
            default:
                throw CatalogModuleError.invalidCatalog("SQLite market query failed")
            }
        }
    }

    private static func isSHA256(_ value: String) -> Bool {
        value.utf8.count == 64 && value.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) }
    }

    private static func isSafeModuleID(_ value: String) -> Bool {
        guard (4...123).contains(value.utf8.count),
              value.utf8.count >= 3,
              value.utf8[value.utf8.index(value.utf8.startIndex, offsetBy: 2)] == 45 else { return false }
        return value.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 46 || $0 == 95
        }
    }

    private static func isSafeKeyID(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 80 && value.utf8.allSatisfy {
            (48...57).contains($0) || (65...90).contains($0) || (97...122).contains($0) || $0 == 45 || $0 == 46 || $0 == 95
        }
    }

    private func sha256(of url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func cleanupDownloadedCandidate(_ candidate: CatalogModuleCandidate) {
        let directory = candidate.databaseURL.deletingLastPathComponent()
        guard directory.lastPathComponent.hasPrefix("HalalFoodEU-CatalogModule-") else { return }
        try? fileManager.removeItem(at: directory)
    }

    private func validateCanonicalManifestData(_ data: Data) throws {
        guard !data.isEmpty, data.count <= Self.maximumSmallFileBytes,
              let object = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw CatalogModuleError.invalidManifest("signed manifest must be a bounded JSON object")
        }
        try Self.validateExactManifestKeys(object)
        var canonical = try JSONSerialization.data(
            withJSONObject: object,
            options: [.sortedKeys, .withoutEscapingSlashes]
        )
        canonical.append(0x0A)
        guard canonical == data else {
            throw CatalogModuleError.invalidManifest("signed manifest is not canonical JSON")
        }
    }

    private static func validateExactTrustPolicyKeys(_ object: [String: Any]) throws {
        let top: Set<String> = ["schemaVersion", "keys", "revokedModuleIDs", "revokedDatabaseSha256"]
        guard Set(object.keys) == top, let keys = object["keys"] as? [[String: Any]] else {
            throw CatalogModuleError.invalidManifest("trust policy fields are not the closed v1 contract")
        }
        let keyFields: Set<String> = ["keyID", "publicKeyBase64", "state"]
        guard keys.allSatisfy({ Set($0.keys) == keyFields }) else {
            throw CatalogModuleError.invalidManifest("trust-key fields are not the closed v1 contract")
        }
    }

    private static func validateExactManifestKeys(_ object: [String: Any]) throws {
        func require(_ value: Any?, keys: Set<String>, label: String) throws {
            guard let dict = value as? [String: Any], Set(dict.keys) == keys else {
                throw CatalogModuleError.invalidManifest("\(label) fields are not the closed v1 contract")
            }
        }
        let top: Set<String> = [
            "schemaVersion", "moduleID", "market", "catalogVersion", "runtimeSchemaVersion",
            "methodologyVersion", "minimumAppVersion", "maximumAppVersion", "database",
            "catalogManifest", "attribution", "counts", "coverage", "sourceSnapshotIdentity",
            "releaseIdentity", "compressedBytes", "installedBytes", "publishedAt", "signingKeyID",
            "supersedesModuleID"
        ]
        guard Set(object.keys) == top else {
            throw CatalogModuleError.invalidManifest("top-level fields are not the closed v1 contract")
        }
        try require(object["database"], keys: ["fileName", "byteCount", "sha256"], label: "database")
        try require(object["catalogManifest"], keys: ["fileName", "sha256"], label: "catalogManifest")
        try require(object["attribution"], keys: ["fileName", "sha256"], label: "attribution")
        try require(
            object["counts"],
            keys: ["products", "uniqueGTINs", "ingredientObservations", "assessments", "retailerEvidence"],
            label: "counts"
        )
        try require(
            object["coverage"],
            keys: ["ingredientCoverageBasisPoints", "freshnessState", "limitations"],
            label: "coverage"
        )
    }
}

private final class CatalogModuleRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var redirectCounts: [Int: Int] = [:]
    private let maximumRedirects = 5
    private let allowedHosts: Set<String> = [
        "api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"
    ]

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection newRequest: URLRequest,
        newResponse: HTTPURLResponse
    ) async -> URLRequest? {
        guard let url = newRequest.url, url.scheme == "https",
              let host = url.host, allowedHosts.contains(host) else { return nil }
        let next = incrementRedirectCount(for: task.taskIdentifier)
        return next <= maximumRedirects ? newRequest : nil
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        clearRedirectCount(for: task.taskIdentifier)
    }

    private func incrementRedirectCount(for taskIdentifier: Int) -> Int {
        lock.lock()
        defer { lock.unlock() }
        let next = redirectCounts[taskIdentifier, default: 0] + 1
        redirectCounts[taskIdentifier] = next
        return next
    }

    private func clearRedirectCount(for taskIdentifier: Int) {
        lock.lock()
        redirectCounts.removeValue(forKey: taskIdentifier)
        lock.unlock()
    }
}

struct GitHubCatalogModuleTransport: CatalogModuleTransport {
    private struct Release: Decodable {
        struct Asset: Decodable {
            let name: String
            let size: Int
            let browser_download_url: URL
        }
        let tag_name: String
        let draft: Bool
        let prerelease: Bool
        let assets: [Asset]
    }

    private static let metadataURL = URL(string: "https://api.github.com/repos/StreamScapeTV/halal-food-eu/releases?per_page=20")!
    private static let expectedAssets: Set<String> = [
        CatalogModuleManager.databaseFileName,
        CatalogModuleManager.catalogManifestFileName,
        CatalogModuleManager.moduleManifestFileName,
        CatalogModuleManager.signatureFileName,
        CatalogModuleManager.attributionFileName,
    ]
    private static let allowedFinalHosts: Set<String> = [
        "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"
    ]

    private let session: URLSession

    init(session: URLSession? = nil) {
        if let session {
            self.session = session
        } else {
            let config = URLSessionConfiguration.ephemeral
            config.timeoutIntervalForRequest = 20
            config.timeoutIntervalForResource = 120
            config.waitsForConnectivity = false
            config.allowsCellularAccess = true
            config.allowsConstrainedNetworkAccess = false
            config.allowsExpensiveNetworkAccess = false
            config.httpMaximumConnectionsPerHost = 2
            self.session = URLSession(
                configuration: config,
                delegate: CatalogModuleRedirectDelegate(),
                delegateQueue: nil
            )
        }
    }

    func availableMarkets() async throws -> [CatalogMarket] {
        let releases = try await fetchReleases()
        var markets = Set<CatalogMarket>()
        for release in releases where !release.draft && !release.prerelease {
            let pieces = release.tag_name.split(separator: "-", maxSplits: 2)
            if pieces.count == 3, pieces[0] == "catalog",
               let market = CatalogMarket(rawValue: String(pieces[1]).uppercased()) {
                markets.insert(market)
            }
        }
        return markets.sorted()
    }

    func latestCandidate(for market: CatalogMarket) async throws -> CatalogModuleCandidate {
        let releases = try await fetchReleases()
        let prefix = "catalog-\(market.rawValue.lowercased())-"
        guard let release = releases.first(where: { !$0.draft && !$0.prerelease && $0.tag_name.hasPrefix(prefix) }) else {
            throw CatalogModuleError.downloadFailed("no verified stable release exists for \(market.rawValue)")
        }
        var byName: [String: Release.Asset] = [:]
        for asset in release.assets where Self.expectedAssets.contains(asset.name) {
            guard byName[asset.name] == nil else { throw CatalogModuleError.downloadFailed("release contains duplicate fixed assets") }
            byName[asset.name] = asset
        }
        guard Set(byName.keys) == Self.expectedAssets else {
            throw CatalogModuleError.downloadFailed("release is missing required fixed assets")
        }

        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("HalalFoodEU-CatalogModule-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        do {
            let database = try await download(byName[CatalogModuleManager.databaseFileName]!, to: root, maximum: CatalogModuleManager.maximumDatabaseBytes)
            let inner = try await download(byName[CatalogModuleManager.catalogManifestFileName]!, to: root, maximum: CatalogModuleManager.maximumSmallFileBytes)
            let manifestURL = try await download(byName[CatalogModuleManager.moduleManifestFileName]!, to: root, maximum: CatalogModuleManager.maximumSmallFileBytes)
            let signatureURL = try await download(byName[CatalogModuleManager.signatureFileName]!, to: root, maximum: 4096)
            let attribution = try await download(byName[CatalogModuleManager.attributionFileName]!, to: root, maximum: CatalogModuleManager.maximumSmallFileBytes)
            return CatalogModuleCandidate(
                releaseIdentity: release.tag_name,
                manifestData: try Data(contentsOf: manifestURL),
                signatureData: try Data(contentsOf: signatureURL),
                databaseURL: database,
                catalogManifestURL: inner,
                attributionURL: attribution
            )
        } catch {
            try? FileManager.default.removeItem(at: root)
            throw error
        }
    }

    private func fetchReleases() async throws -> [Release] {
        var request = URLRequest(url: Self.metadataURL)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.setValue("HalalFoodEU/0.1", forHTTPHeaderField: "User-Agent")
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              data.count <= 2 * 1024 * 1024 else {
            throw CatalogModuleError.downloadFailed("GitHub release metadata was unavailable or oversized")
        }
        return try JSONDecoder().decode([Release].self, from: data)
    }

    private func download(_ asset: Release.Asset, to root: URL, maximum: Int) async throws -> URL {
        guard asset.size > 0, asset.size <= maximum,
              asset.browser_download_url.scheme == "https",
              asset.browser_download_url.host == "github.com" else {
            throw CatalogModuleError.downloadFailed("release asset metadata violates bounds")
        }
        var request = URLRequest(url: asset.browser_download_url)
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        request.setValue("HalalFoodEU/0.1", forHTTPHeaderField: "User-Agent")
        let (temporaryURL, response) = try await session.download(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200,
              let finalHost = response.url?.host, Self.allowedFinalHosts.contains(finalHost),
              response.url?.scheme == "https" else {
            throw CatalogModuleError.downloadFailed("release asset redirect target was rejected")
        }
        let values = try temporaryURL.resourceValues(forKeys: [.fileSizeKey])
        guard values.fileSize == asset.size, asset.size <= maximum else {
            throw CatalogModuleError.downloadFailed("release asset byte count differs from metadata")
        }
        let destination = root.appendingPathComponent(asset.name, isDirectory: false)
        try FileManager.default.moveItem(at: temporaryURL, to: destination)
        return destination
    }
}
