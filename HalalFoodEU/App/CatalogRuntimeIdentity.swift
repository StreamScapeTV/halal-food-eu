import Foundation

struct CatalogRuntimeIdentity: Sendable {
    let catalogVersion: String
}

enum CatalogRuntimeIdentityLoader {
    private struct ManifestIdentity: Decodable {
        let catalogVersion: String
    }

    static func load(manifestURL: URL) throws -> CatalogRuntimeIdentity {
        let data = try Data(contentsOf: manifestURL, options: [.mappedIfSafe])
        let manifest = try JSONDecoder().decode(ManifestIdentity.self, from: data)
        let version = manifest.catalogVersion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !version.isEmpty else {
            throw ProductCatalogError.invalidRecord("catalog manifest version is empty")
        }
        return CatalogRuntimeIdentity(catalogVersion: version)
    }
}
