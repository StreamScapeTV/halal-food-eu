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
    private var historyConsentRevision: UInt64 = 0
    private var cameraHistoryWriteTasks: [UUID: Task<Void, Never>] = [:]

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
            if historyEnabled != values.0 {
                invalidateCameraHistoryConsent()
            }
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
        if previous != enabled {
            // A consent transition permanently invalidates every camera event
            // captured under the preceding generation. Re-enabling history must
            // never resurrect a scan that crossed a revocation boundary.
            invalidateCameraHistoryConsent()
        }
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

    func cameraHistoryConsentToken() -> UInt64? {
        historyEnabled ? historyConsentRevision : nil
    }

    func recordCameraScan(_ result: ProductLookupResult, consentToken: UInt64) {
        guard historyEnabled, consentToken == historyConsentRevision else { return }

        let catalogVersion = result.catalogVersion.isEmpty ? (result.product?.catalogVersion ?? currentCatalogVersion) : result.catalogVersion
        guard !catalogVersion.isEmpty else {
            errorMessage = String(
                localized: "The current catalog version could not be identified, so this scan was not saved.",
                table: "UserLibrary"
            )
            return
        }
        let timestamp = now()
        let marker = SavedProductVersionMarker(product: result.product)
        let taskID = UUID()

        let task = Task { [weak self, store] in
            guard let self else { return }
            defer { cameraHistoryWriteTasks[taskID] = nil }
            guard historyEnabled,
                  historyConsentRevision == consentToken,
                  !Task.isCancelled else { return }
            do {
                // SQLiteUserProductLibrary also checks Task cancellation and the
                // persisted opt-in immediately before its transaction. Together
                // with generation invalidation this closes revoke/re-enable races.
                try await store.recordScan(
                    market: result.market,
                    barcode: result.barcode,
                    scannedAt: timestamp,
                    catalogVersion: catalogVersion,
                    versionMarker: marker
                )
                try Task.checkCancellation()
                guard historyEnabled, historyConsentRevision == consentToken else { return }
                history = try await store.history(limit: UserProductLibraryPolicy.maximumHistoryEntries)
                errorMessage = nil
            } catch is CancellationError {
                return
            } catch {
                errorMessage = error.localizedDescription
            }
        }
        cameraHistoryWriteTasks[taskID] = task
    }

    func isFavorite(_ barcode: Barcode, market: CatalogMarket = .germany) -> Bool {
        favorites.contains(where: { $0.market == market && $0.barcode == barcode })
    }

    func toggleFavorite(_ product: ProductRecord, market: CatalogMarket? = nil) async {
        let resolvedMarket = market ?? product.details.flatMap { CatalogMarket(rawValue: $0.market) } ?? .germany
        let shouldFavorite = !isFavorite(product.barcode, market: resolvedMarket)
        do {
            try await store.setFavorite(
                market: resolvedMarket,
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
                market: favorite.market,
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

    private func invalidateCameraHistoryConsent() {
        historyConsentRevision &+= 1
        for task in cameraHistoryWriteTasks.values {
            task.cancel()
        }
        cameraHistoryWriteTasks.removeAll()
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
