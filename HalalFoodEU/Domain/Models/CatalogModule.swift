import Foundation

struct CatalogMarket: RawRepresentable, Codable, Hashable, Identifiable, Comparable, Sendable {
    let rawValue: String

    static let germany = try! CatalogMarket(validating: "DE")

    var id: String { rawValue }

    init?(rawValue: String) {
        guard let value = try? CatalogMarket(validating: rawValue) else { return nil }
        self = value
    }

    init(validating value: String) throws {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard normalized.utf8.count == 2,
              normalized.utf8.allSatisfy({ (65...90).contains($0) }) else {
            throw CatalogModuleError.invalidManifest("market must be an ISO alpha-2 code")
        }
        rawValue = normalized
    }

    static func < (lhs: CatalogMarket, rhs: CatalogMarket) -> Bool {
        lhs.rawValue < rhs.rawValue
    }
}

struct CatalogModuleManifest: Codable, Equatable, Sendable {
    struct Database: Codable, Equatable, Sendable {
        let fileName: String
        let byteCount: Int
        let sha256: String
    }

    struct FileDigest: Codable, Equatable, Sendable {
        let fileName: String
        let sha256: String
    }

    struct Counts: Codable, Equatable, Sendable {
        let products: Int
        let uniqueGTINs: Int
        let ingredientObservations: Int
        let assessments: Int
        let retailerEvidence: Int
    }

    struct Coverage: Codable, Equatable, Sendable {
        let ingredientCoverageBasisPoints: Int
        let freshnessState: String
        let limitations: [String]
    }

    let schemaVersion: Int
    let moduleID: String
    let market: CatalogMarket
    let catalogVersion: String
    let runtimeSchemaVersion: Int
    let methodologyVersion: String
    let minimumAppVersion: String
    let maximumAppVersion: String
    let database: Database
    let catalogManifest: FileDigest
    let attribution: FileDigest
    let counts: Counts
    let coverage: Coverage
    let sourceSnapshotIdentity: String
    let releaseIdentity: String
    let compressedBytes: Int
    let installedBytes: Int
    let publishedAt: String
    let signingKeyID: String
    let supersedesModuleID: String?
}

struct CatalogModuleTrustPolicy: Codable, Equatable, Sendable {
    struct Key: Codable, Equatable, Sendable {
        enum State: String, Codable, Sendable {
            case active
            case retired
            case revoked
        }

        let keyID: String
        let publicKeyBase64: String
        let state: State
    }

    let schemaVersion: Int
    let keys: [Key]
    let revokedModuleIDs: [String]
    let revokedDatabaseSha256: [String]
}

struct CatalogModuleCandidate: Sendable {
    let releaseIdentity: String
    let manifestData: Data
    let signatureData: Data
    let databaseURL: URL
    let catalogManifestURL: URL
    let attributionURL: URL
}

struct InstalledCatalogModule: Identifiable, Sendable {
    var id: String { manifest.moduleID }

    let manifest: CatalogModuleManifest
    let directoryURL: URL
    let databaseURL: URL
    let catalogManifestURL: URL
    let attributionURL: URL
    let moduleManifestURL: URL
    let signatureURL: URL
}

struct CatalogModuleStatus: Equatable, Sendable {
    let market: CatalogMarket
    let catalogVersion: String?
    let installedBytes: Int?
    let coverageLimitations: [String]
    let isBundledFallback: Bool
}

enum CatalogModuleError: LocalizedError, Equatable, Sendable {
    case updatesUnavailable
    case invalidManifest(String)
    case untrustedKey(String)
    case revoked(String)
    case invalidSignature
    case digestMismatch(String)
    case incompatibleApp(minimum: String, maximum: String, actual: String)
    case wrongMarket(expected: String, actual: String)
    case invalidCatalog(String)
    case downloadFailed(String)
    case installFailed(String)
    case unavailableMarket(String)
    case replayedOrOutOfOrder(String)

    var errorDescription: String? {
        switch self {
        case .updatesUnavailable:
            "Signed catalog updates are not configured in this app build."
        case let .invalidManifest(message):
            "The catalog update manifest is invalid: \(message)"
        case let .untrustedKey(keyID):
            "The catalog update signing key is not trusted: \(keyID)"
        case let .revoked(message):
            "The catalog update has been revoked: \(message)"
        case .invalidSignature:
            "The catalog update signature is invalid."
        case let .digestMismatch(file):
            "The catalog update file failed integrity verification: \(file)"
        case let .incompatibleApp(minimum, maximum, actual):
            "The catalog update supports app versions \(minimum) through \(maximum), not \(actual)."
        case let .wrongMarket(expected, actual):
            "The catalog update is for \(actual), but \(expected) was requested."
        case let .invalidCatalog(message):
            "The downloaded catalog is invalid: \(message)"
        case let .downloadFailed(message):
            "The catalog update could not be downloaded: \(message)"
        case let .installFailed(message):
            "The catalog update could not be installed: \(message)"
        case let .unavailableMarket(market):
            "No verified offline catalog is installed for market \(market)."
        case let .replayedOrOutOfOrder(message):
            "The catalog update was rejected because it is not the next trusted version: \(message)"
        }
    }
}
