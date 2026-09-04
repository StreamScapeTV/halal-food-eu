import Foundation

protocol UserProductLibraryStore: Sendable {
    func isHistoryEnabled() async throws -> Bool
    func setHistoryEnabled(_ enabled: Bool) async throws

    func recordScan(
        barcode: Barcode,
        scannedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker
    ) async throws
    func history(limit: Int) async throws -> [ScanHistoryEntry]
    func deleteHistoryEntry(id: Int64) async throws
    func clearHistory() async throws

    func favorites() async throws -> [FavoriteProduct]
    func favorite(for barcode: Barcode) async throws -> FavoriteProduct?
    func setFavorite(
        barcode: Barcode,
        savedAt: Date,
        catalogVersion: String,
        versionMarker: SavedProductVersionMarker,
        isFavorite: Bool
    ) async throws
}

enum UserProductLibraryError: LocalizedError, Sendable {
    case unavailable(String)
    case incompatibleStore(expected: Int, actual: Int)
    case invalidRecord(String)
    case queryFailed(String)

    var errorDescription: String? {
        switch self {
        case let .unavailable(message):
            "Your local history and favorites are unavailable: \(message)"
        case let .incompatibleStore(expected, actual):
            "Your local history store uses schema \(actual), but this app supports schema \(expected)."
        case let .invalidRecord(message):
            "Your local history store contains an invalid record: \(message)"
        case let .queryFailed(message):
            "Your local history store could not be read or updated: \(message)"
        }
    }
}
