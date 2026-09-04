import CryptoKit
import Foundation

enum UserProductLibraryPolicy {
    static let maximumHistoryEntries = 200
    static let fingerprintSchemaVersion = 1
}

struct SavedProductVersionMarker: Codable, Equatable, Sendable {
    let fingerprintSchemaVersion: Int
    let wasPresent: Bool
    let recordFingerprint: String?

    init(product: ProductRecord?) {
        fingerprintSchemaVersion = UserProductLibraryPolicy.fingerprintSchemaVersion
        guard let product else {
            wasPresent = false
            recordFingerprint = nil
            return
        }

        wasPresent = true
        recordFingerprint = Self.fingerprint(product)
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let schemaVersion = try container.decode(Int.self, forKey: .fingerprintSchemaVersion)
        let present = try container.decode(Bool.self, forKey: .wasPresent)
        let fingerprint = try container.decodeIfPresent(String.self, forKey: .recordFingerprint)

        guard schemaVersion == UserProductLibraryPolicy.fingerprintSchemaVersion else {
            throw DecodingError.dataCorruptedError(
                forKey: .fingerprintSchemaVersion,
                in: container,
                debugDescription: "Unsupported saved-product fingerprint schema"
            )
        }
        if present {
            guard let fingerprint, Self.isLowercaseSHA256(fingerprint) else {
                throw DecodingError.dataCorruptedError(
                    forKey: .recordFingerprint,
                    in: container,
                    debugDescription: "Present saved products require a lowercase SHA-256 fingerprint"
                )
            }
        } else if fingerprint != nil {
            throw DecodingError.dataCorruptedError(
                forKey: .recordFingerprint,
                in: container,
                debugDescription: "Previously missing products must not carry a record fingerprint"
            )
        }

        fingerprintSchemaVersion = schemaVersion
        wasPresent = present
        recordFingerprint = fingerprint
    }

    private static func isLowercaseSHA256(_ value: String) -> Bool {
        guard value.utf8.count == 64 else { return false }
        return value.utf8.allSatisfy { byte in
            (48...57).contains(byte) || (97...102).contains(byte)
        }
    }

    private static func fingerprint(_ product: ProductRecord) -> String {
        var builder = FingerprintBuilder()
        builder.append(product.barcode.rawValue)
        builder.append(product.name)
        builder.append(product.brand)

        if let observation = product.observation {
            builder.append(true)
            builder.append(observation.contentHash)
            builder.append(observation.languageCode)
            builder.append(observation.observedAt)
            builder.append(observation.freshness.rawValue)
            builder.append(observation.source.name)
            builder.append(observation.source.kind)
            builder.append(observation.source.reference)
            builder.append(observation.source.license)
            builder.append(observation.source.retrievedAt)
            builder.append(observation.source.attribution)
            builder.append(observation.details?.allergensText)
            builder.append(observation.details?.tracesText)
            builder.append(observation.details?.retrievedAt)
            builder.append(observation.details?.verificationState.rawValue)
        } else {
            builder.append(false)
        }

        builder.append(product.assessment.status.rawValue)
        builder.append(product.assessment.summary)
        builder.append(product.assessment.methodologyVersion)
        builder.append(product.assessment.reviewedAt)
        builder.append(product.assessment.assessedAt)
        builder.append(product.assessment.recheckAt)
        builder.append(product.assessment.approvedReviewerCount)

        let reasons = product.assessment.reasons.map {
            "\($0.code)|\($0.title)|\($0.detail)|\($0.ingredient ?? "")|\($0.severity.rawValue)"
        }.sorted()
        builder.append(reasons)

        let certifications = product.assessment.certifications.map {
            let validFrom = $0.validFrom.map(Self.dateToken) ?? ""
            let validUntil = $0.validUntil.map(Self.dateToken) ?? ""
            let checked = $0.lastCheckedAt.map(Self.dateToken) ?? ""
            let retrieved = Self.dateToken($0.source.retrievedAt)
            return "\($0.certifyingBody)|\($0.scheme ?? "")|\($0.certificateReference)|\($0.scope)|\(validFrom)|\(validUntil)|\(checked)|\($0.source.name)|\($0.source.kind)|\($0.source.reference)|\($0.source.license)|\(retrieved)|\($0.source.attribution ?? "")"
        }.sorted()
        builder.append(certifications)

        if let details = product.details {
            builder.append(true)
            builder.append(details.market)
            builder.append(details.brandOwner)
            builder.append(details.quantity)
            builder.append(details.conflictFlags.sorted())
            let retailerEvidence = details.retailerEvidence.map {
                let observed = $0.observedAt.map(Self.dateToken) ?? ""
                let snapshot = $0.snapshotAt.map(Self.dateToken) ?? ""
                let retrieved = Self.dateToken($0.source.retrievedAt)
                return "\($0.kind.rawValue)|\($0.retailerKey)|\(observed)|\(snapshot)|\($0.scope ?? "")|\($0.locationID ?? "")|\($0.limitations)|\($0.source.name)|\($0.source.kind)|\($0.source.reference)|\($0.source.license)|\(retrieved)|\($0.source.attribution ?? "")"
            }.sorted()
            builder.append(retailerEvidence)
        } else {
            builder.append(false)
        }

        let digest = SHA256.hash(data: builder.data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    private static func dateToken(_ date: Date) -> String {
        String(date.timeIntervalSinceReferenceDate.bitPattern, radix: 16)
    }
}

private struct FingerprintBuilder {
    private(set) var data = Data()

    mutating func append(_ value: Bool) {
        data.append(value ? 1 : 0)
    }

    mutating func append(_ value: Int?) {
        guard let value else {
            data.append(0)
            return
        }
        data.append(1)
        var bigEndian = Int64(value).bigEndian
        withUnsafeBytes(of: &bigEndian) { data.append(contentsOf: $0) }
    }

    mutating func append(_ value: Date?) {
        guard let value else {
            data.append(0)
            return
        }
        data.append(1)
        var bigEndian = value.timeIntervalSinceReferenceDate.bitPattern.bigEndian
        withUnsafeBytes(of: &bigEndian) { data.append(contentsOf: $0) }
    }

    mutating func append(_ value: String?) {
        guard let value else {
            data.append(0)
            return
        }
        data.append(1)
        let bytes = Data(value.utf8)
        var length = UInt64(bytes.count).bigEndian
        withUnsafeBytes(of: &length) { data.append(contentsOf: $0) }
        data.append(bytes)
    }

    mutating func append(_ values: [String]) {
        var count = UInt64(values.count).bigEndian
        withUnsafeBytes(of: &count) { data.append(contentsOf: $0) }
        for value in values { append(value) }
    }
}

enum SavedProductChangeState: Equatable, Sendable {
    case unchanged
    case changed
    case noLongerPresent
    case nowAvailable
}

extension SavedProductVersionMarker {
    func comparison(with currentProduct: ProductRecord?) -> SavedProductChangeState {
        let current = SavedProductVersionMarker(product: currentProduct)
        switch (wasPresent, current.wasPresent) {
        case (true, false):
            return .noLongerPresent
        case (false, true):
            return .nowAvailable
        default:
            guard fingerprintSchemaVersion == current.fingerprintSchemaVersion else {
                return .changed
            }
            return recordFingerprint == current.recordFingerprint ? .unchanged : .changed
        }
    }
}

struct ScanHistoryEntry: Identifiable, Equatable, Sendable {
    let id: Int64
    let barcode: Barcode
    let scannedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker
}

struct FavoriteProduct: Identifiable, Equatable, Sendable {
    var id: String { barcode.rawValue }

    let barcode: Barcode
    let savedAt: Date
    let catalogVersion: String
    let versionMarker: SavedProductVersionMarker
}
