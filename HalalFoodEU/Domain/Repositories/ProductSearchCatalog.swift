import Foundation

protocol ProductSearchCatalog: Sendable {
    func search(query: String, limit: Int, offset: Int) async throws -> ProductSearchPage
}
