import Foundation
import Observation

@MainActor
@Observable
final class ProductSearchViewModel {
    enum State: Equatable {
        case idle
        case searching
        case results
        case empty
        case failed(String)
    }

    var query = ""
    private(set) var state: State = .idle
    private(set) var results: [ProductSearchResult] = []
    private(set) var hasMore = false
    private(set) var isLoadingMore = false

    private let searchProducts: SearchProducts
    private let debounceDuration: Duration
    private var searchTask: Task<Void, Never>?
    private var generation = 0

    init(
        searchProducts: SearchProducts,
        debounceDuration: Duration = .milliseconds(250)
    ) {
        self.searchProducts = searchProducts
        self.debounceDuration = debounceDuration
    }

    func queryDidChange() {
        scheduleSearch(debounced: true)
    }

    func submit() {
        scheduleSearch(debounced: false)
    }

    func loadMore() {
        guard hasMore, !isLoadingMore, !results.isEmpty else { return }
        searchTask?.cancel()
        generation += 1
        let requestGeneration = generation
        let requestedQuery = query
        let offset = results.count
        isLoadingMore = true

        searchTask = Task { [weak self, searchProducts] in
            do {
                let page = try await searchProducts(
                    requestedQuery,
                    limit: SearchProducts.defaultPageSize,
                    offset: offset
                )
                try Task.checkCancellation()
                guard let self, generation == requestGeneration else { return }
                var known = Set(results.map(\.barcode.rawValue))
                results.append(contentsOf: page.results.filter { known.insert($0.barcode.rawValue).inserted })
                hasMore = page.hasMore
                isLoadingMore = false
                state = results.isEmpty ? .empty : .results
            } catch is CancellationError {
                return
            } catch {
                guard let self, generation == requestGeneration else { return }
                isLoadingMore = false
                state = .failed(error.localizedDescription)
            }
        }
    }

    func reset() {
        searchTask?.cancel()
        generation += 1
        query = ""
        results = []
        hasMore = false
        isLoadingMore = false
        state = .idle
    }

    private func scheduleSearch(debounced: Bool) {
        searchTask?.cancel()
        generation += 1
        let requestGeneration = generation
        let requestedQuery = query
        let trimmed = requestedQuery.trimmingCharacters(in: .whitespacesAndNewlines)

        results = []
        hasMore = false
        isLoadingMore = false
        guard !trimmed.isEmpty else {
            state = .idle
            return
        }
        state = .searching

        searchTask = Task { [weak self, searchProducts, debounceDuration] in
            do {
                if debounced, debounceDuration > .zero {
                    try await Task.sleep(for: debounceDuration)
                }
                let page = try await searchProducts(requestedQuery)
                try Task.checkCancellation()
                guard let self, generation == requestGeneration else { return }
                results = page.results
                hasMore = page.hasMore
                state = page.results.isEmpty ? .empty : .results
            } catch is CancellationError {
                return
            } catch {
                guard let self, generation == requestGeneration else { return }
                state = .failed(error.localizedDescription)
            }
        }
    }
}
