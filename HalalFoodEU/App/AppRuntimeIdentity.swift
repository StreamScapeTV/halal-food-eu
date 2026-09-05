import Foundation

struct AppRuntimeIdentity: Equatable, Sendable {
    let version: String?
    let build: String?
    let catalogVersion: String?

    init(version: String?, build: String?, catalogVersion: String?) {
        self.version = Self.normalized(version)
        self.build = Self.normalized(build)
        self.catalogVersion = Self.normalized(catalogVersion)
    }

    init(bundle: Bundle = .main) {
        let catalogVersion = bundle
            .url(forResource: "catalog-manifest", withExtension: "json")
            .flatMap { try? CatalogRuntimeIdentityLoader.load(manifestURL: $0).catalogVersion }
        self.init(
            version: bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
            build: bundle.object(forInfoDictionaryKey: "CFBundleVersion") as? String,
            catalogVersion: catalogVersion
        )
    }

    private static func normalized(_ value: String?) -> String? {
        guard let value else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}
