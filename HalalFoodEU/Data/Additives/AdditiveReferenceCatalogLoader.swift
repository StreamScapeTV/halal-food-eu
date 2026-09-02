import Foundation

enum AdditiveReferenceCatalogLoader {
    static func load(bundle: Bundle = .main) throws -> AdditiveReferenceCatalog {
        guard let url = bundle.url(forResource: "additive-identities-v1", withExtension: "json") else {
            throw AdditiveReferenceCatalogError.missingResource
        }
        let data = try Data(contentsOf: url, options: [.mappedIfSafe])
        let catalog = try JSONDecoder().decode(AdditiveReferenceCatalog.self, from: data)
        try catalog.validateForRuntime()
        return catalog
    }
}
