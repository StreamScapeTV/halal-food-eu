import Foundation
import Observation

@MainActor
@Observable
final class UserProductLibraryViewModel {
    private(set) var historyEnabled = false
    private(set) var history: [ScanHistoryEntry] = []
    private(set) var favorites: [FavoriteProduct] = []
    private(set) var isLoading = false
    var errorMessage: String?

    private let store: any UserProductLibraryStore
    private let resolveSavedProduct: ResolveSavedProduct
    private let currentCatalogVersion: String
    private let now: @Sendable () -> Date

    init(
        store: any UserProductLibraryStore,
        resolveSavedProduct: ResolveSavedProduct,
        currentCatalogVersion: String,
        now: @escaping @Sendable () -> Date = { Date() }
    ) {
        self.store = store
        self.resolveSavedProduct = resolveSavedProduct
        self.currentCatalogVersion = currentCatalogVersion
        self.now = now
    }

    func load() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let enabled = store.isHistoryEnabled()
            async let loadedHistory = store.history(limit: UserProductLibraryPolicy.maximumHistoryEntries)
            async let loadedFavorites = store.favorites()
            let values = try await (enabled, loadedHistory, loadedFavorites)
            historyEnabled = values.0
            history = values.1
            favorites = values.2
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func setHistoryEnabled(_ enabled: Bool) async {
        let previous = historyEnabled
        historyEnabled = enabled
        do {
            try await store.setHistoryEnabled(enabled)
            errorMessage = nil
        } catch is CancellationError {
            historyEnabled = previous
        } catch {
            historyEnabled = previous
            errorMessage = error.localizedDescription
        }
    }

    func recordCameraScan(_ result: ProductLookupResult) {
        // Capture consent at the physical scan event boundary. The store also
        // checks its persisted preference, but this guard prevents a scan made
        // while history was off from being admitted if the user enables history
        // before the asynchronous write reaches the actor.
        guard historyEnabled else { return }

        let catalogVersion = result.product?.catalogVersion ?? currentCatalogVersion
        guard !catalogVersion.isEmpty else {
            errorMessage = String(
                localized: "The current catalog version could not be identified, so this scan was not saved.",
                table: "UserLibrary"
            )
            return
        }
        let timestamp = now()
        let marker = SavedProductVersionMarker(product: result.product)

        Task { [weak self, store] in
            do {
                try await store.recordScan(
                    barcode: result.barcode,
                    scannedAt: timestamp,
                    catalogVersion: catalogVersion,
                    versionMarker: marker
                )
                guard let self else { return }
                if historyEnabled {
                    history = try await store.history(limit: UserProductLibraryPolicy.maximumHistoryEntries)
                }
                errorMessage = nil
            } catch is CancellationError {
                return
            } catch {
                self?.errorMessage = error.localizedDescription
            }
        }
    }

    func isFavorite(_ barcode: Barcode) -> Bool {
        favorites.contains(where: { $0.barcode == barcode })
    }

    func toggleFavorite(_ product: ProductRecord) async {
        let shouldFavorite = !isFavorite(product.barcode)
        do {
            try await store.setFavorite(
                barcode: product.barcode,
                savedAt: now(),
                catalogVersion: product.catalogVersion,
                versionMarker: SavedProductVersionMarker(product: product),
                isFavorite: shouldFavorite
            )
            favorites = try await store.favorites()
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func removeFavorite(_ favorite: FavoriteProduct) async {
        do {
            try await store.setFavorite(
                barcode: favorite.barcode,
                savedAt: favorite.savedAt,
                catalogVersion: favorite.catalogVersion,
                versionMarker: favorite.versionMarker,
                isFavorite: false
            )
            favorites = try await store.favorites()
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func deleteHistoryEntry(_ entry: ScanHistoryEntry) async {
        do {
            try await store.deleteHistoryEntry(id: entry.id)
            history = try await store.history(limit: UserProductLibraryPolicy.maximumHistoryEntries)
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func clearHistory() async {
        do {
            try await store.clearHistory()
            history = []
            errorMessage = nil
        } catch is CancellationError {
            return
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func makeDetailViewModel(for reference: SavedProductReference) -> SavedProductDetailViewModel {
        SavedProductDetailViewModel(reference: reference, resolveSavedProduct: resolveSavedProduct)
    }
}

@MainActor
@Observable
final class SavedProductDetailViewModel {
    enum State: Equatable {
        case idle
        case loading
        case loaded(ResolvedSavedProduct)
        case failed(String)
    }

    let reference: SavedProductReference
    private(set) var state: State = .idle

    private let resolveSavedProduct: ResolveSavedProduct
    private var loadTask: Task<Void, Never>?

    init(reference: SavedProductReference, resolveSavedProduct: ResolveSavedProduct) {
        self.reference = reference
        self.resolveSavedProduct = resolveSavedProduct
    }

    func load() {
        loadTask?.cancel()
        state = .loading
        loadTask = Task { [weak self, resolveSavedProduct, reference] in
            do {
                let resolved = try await resolveSavedProduct(reference)
                try Task.checkCancellation()
                self?.state = .loaded(resolved)
            } catch is CancellationError {
                return
            } catch {
                guard !Task.isCancelled else { return }
                self?.state = .failed(error.localizedDescription)
            }
        }
    }

    func cancel() {
        loadTask?.cancel()
    }
}
