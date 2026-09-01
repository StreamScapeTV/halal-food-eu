import Foundation

struct ProductEvidenceSubmissionRuntimeConfiguration: Equatable, Sendable {
    let submission: ProductEvidenceSubmissionConfiguration
    let appVersion: String
    let catalogVersion: String
}

enum ProductEvidenceSubmissionConfigurationLoadError: LocalizedError, Equatable, Sendable {
    case missingPublicConfiguration
    case invalidPublicConfiguration
    case missingCatalogVersion

    var errorDescription: String? {
        switch self {
        case .missingPublicConfiguration:
            String(localized: "Product evidence submission is unavailable because the public project configuration is missing.")
        case .invalidPublicConfiguration:
            String(localized: "Product evidence submission is unavailable because the public project configuration is invalid.")
        case .missingCatalogVersion:
            String(localized: "Product evidence submission is unavailable because the bundled catalog version could not be read.")
        }
    }
}

enum ProductEvidenceSubmissionConfigurationLoader {
    private struct PublicConfiguration: Decodable {
        let schemaVersion: Int
        let publicValues: [String: String]
    }

    private struct CatalogManifestVersion: Decodable {
        let catalogVersion: String
    }

    static func load(
        bundle: Bundle,
        catalogManifestURL: URL
    ) throws -> ProductEvidenceSubmissionRuntimeConfiguration {
        guard let configURL = bundle.url(
            forResource: "public-project-configuration-v1",
            withExtension: "json"
        ) else {
            throw ProductEvidenceSubmissionConfigurationLoadError.missingPublicConfiguration
        }
        let submission = try decodePublicConfiguration(Data(contentsOf: configURL))
        let catalogVersion = try decodeCatalogVersion(Data(contentsOf: catalogManifestURL))
        let appVersion = (bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
            .flatMap { value in value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : value }
            ?? "0.0.0"
        return ProductEvidenceSubmissionRuntimeConfiguration(
            submission: submission,
            appVersion: appVersion,
            catalogVersion: catalogVersion
        )
    }

    static func decodePublicConfiguration(_ data: Data) throws -> ProductEvidenceSubmissionConfiguration {
        let decoded: PublicConfiguration
        do {
            decoded = try JSONDecoder().decode(PublicConfiguration.self, from: data)
        } catch {
            throw ProductEvidenceSubmissionConfigurationLoadError.invalidPublicConfiguration
        }
        guard decoded.schemaVersion == 1,
              decoded.publicValues.count == 3,
              let email = decoded.publicValues["PRODUCT_SUBMISSION_EMAIL"] else {
            throw ProductEvidenceSubmissionConfigurationLoadError.invalidPublicConfiguration
        }
        do {
            return try ProductEvidenceSubmissionConfiguration(destinationEmail: email)
        } catch {
            throw ProductEvidenceSubmissionConfigurationLoadError.invalidPublicConfiguration
        }
    }

    static func decodeCatalogVersion(_ data: Data) throws -> String {
        let decoded: CatalogManifestVersion
        do {
            decoded = try JSONDecoder().decode(CatalogManifestVersion.self, from: data)
        } catch {
            throw ProductEvidenceSubmissionConfigurationLoadError.missingCatalogVersion
        }
        let value = decoded.catalogVersion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty, value.count <= 80 else {
            throw ProductEvidenceSubmissionConfigurationLoadError.missingCatalogVersion
        }
        return value
    }
}
