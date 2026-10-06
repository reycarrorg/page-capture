import CoreGraphics
import Foundation
import ImageIO

public struct PageRecord: Equatable, Sendable {
    public let number: Int
    public let url: URL

    public init(number: Int, url: URL) {
        self.number = number
        self.url = url
    }
}

public enum PageCaptureError: LocalizedError, Equatable {
    case noPages
    case invalidPNG
    case duplicate
    case cannotCreateSession
    case exportFailed(String)

    public var errorDescription: String? {
        switch self {
        case .noPages: return "Capture at least one page before exporting."
        case .invalidPNG: return "The page is not a valid PNG image."
        case .duplicate: return "This page is an exact duplicate of a captured page."
        case .cannotCreateSession: return "Could not create a unique session directory."
        case .exportFailed(let message): return message
        }
    }
}

public final class PageCaptureSession {
    public let directory: URL
    public private(set) var pages: [PageRecord] = []
    public var pageCount: Int { pages.count }

    public init(root: URL, date: Date = Date()) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let stem = "Session-\(formatter.string(from: date))"
        var candidate = root.appendingPathComponent(stem, isDirectory: true)
        var createdDirectory: URL?
        for suffix in 0...999 {
            do {
                try FileManager.default.createDirectory(at: candidate, withIntermediateDirectories: false)
                try FileManager.default.createDirectory(
                    at: candidate.appendingPathComponent("Undone", isDirectory: true),
                    withIntermediateDirectories: false
                )
                createdDirectory = candidate
                break
            } catch CocoaError.fileWriteFileExists {
                candidate = root.appendingPathComponent("\(stem)-\(suffix + 1)", isDirectory: true)
            } catch {
                throw PageCaptureError.cannotCreateSession
            }
        }
        guard let createdDirectory else { throw PageCaptureError.cannotCreateSession }
        directory = createdDirectory
    }

    public static func open(directory: URL) throws -> PageCaptureSession {
        var records: [PageRecord] = []
        let numberedURLs = try FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        ).compactMap { url -> (number: Int, url: URL)? in
            guard url.pathExtension.lowercased() == "png",
                  url.deletingPathExtension().lastPathComponent.hasPrefix("page_"),
                  let number = Int(url.deletingPathExtension().lastPathComponent.dropFirst(5)),
                  number > 0 else { return nil }
            return (number, url)
        }.sorted { $0.number < $1.number }
        for item in numberedURLs {
            let url = item.url
            guard try ImageSignature(contentsOf: url) != nil else { throw PageCaptureError.invalidPNG }
            records.append(PageRecord(number: item.number, url: url))
        }
        let session = PageCaptureSession(directory: directory, pages: records)
        try FileManager.default.createDirectory(
            at: directory.appendingPathComponent("Undone", isDirectory: true),
            withIntermediateDirectories: true
        )
        return session
    }

    private init(directory: URL, pages: [PageRecord]) {
        self.directory = directory
        self.pages = pages
    }

    @discardableResult
    public func addPNG(_ data: Data) throws -> PageRecord {
        guard let signature = ImageSignature(data: data) else {
            throw PageCaptureError.invalidPNG
        }
        if try pages.contains(where: { try ImageSignature(contentsOf: $0.url) == signature }) {
            throw PageCaptureError.duplicate
        }
        var number = (pages.map(\.number).max() ?? 0) + 1
        var url = directory.appendingPathComponent(String(format: "page_%04d.png", number))
        while FileManager.default.fileExists(atPath: url.path) {
            number += 1
            url = directory.appendingPathComponent(String(format: "page_%04d.png", number))
        }
        try data.write(to: url, options: .atomic)
        let record = PageRecord(number: number, url: url)
        pages.append(record)
        return record
    }

    @discardableResult
    public func undoLatest() throws -> PageRecord? {
        guard let latest = pages.last else { return nil }
        let undone = directory.appendingPathComponent("Undone", isDirectory: true)
        try FileManager.default.createDirectory(at: undone, withIntermediateDirectories: true)
        var destination = undone.appendingPathComponent(latest.url.lastPathComponent)
        var suffix = 1
        while FileManager.default.fileExists(atPath: destination.path) {
            destination = undone.appendingPathComponent(
                "\(latest.url.deletingPathExtension().lastPathComponent)-\(suffix).png"
            )
            suffix += 1
        }
        try FileManager.default.moveItem(at: latest.url, to: destination)
        pages.removeLast()
        return PageRecord(number: latest.number, url: destination)
    }

    public func exportPDF(to destination: URL) throws {
        guard !pages.isEmpty else { throw PageCaptureError.noPages }
        let parent = destination.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        let temporary = parent.appendingPathComponent(
            ".\(destination.deletingPathExtension().lastPathComponent)-\(UUID().uuidString).building.pdf"
        )
        do {
            guard let context = CGContext(temporary as CFURL, mediaBox: nil, nil) else {
                throw PageCaptureError.exportFailed("Could not create the temporary PDF.")
            }
            for page in pages {
                guard let source = CGImageSourceCreateWithURL(page.url as CFURL, nil),
                      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
                    throw PageCaptureError.invalidPNG
                }
                var box = CGRect(x: 0, y: 0, width: image.width, height: image.height)
                context.beginPage(mediaBox: &box)
                context.draw(image, in: box)
                context.endPage()
            }
            context.closePDF()
            if FileManager.default.fileExists(atPath: destination.path) {
                _ = try FileManager.default.replaceItemAt(destination, withItemAt: temporary)
            } else {
                try FileManager.default.moveItem(at: temporary, to: destination)
            }
        } catch let error as PageCaptureError {
            try? FileManager.default.removeItem(at: temporary)
            throw error
        } catch {
            try? FileManager.default.removeItem(at: temporary)
            throw PageCaptureError.exportFailed(error.localizedDescription)
        }
    }
}

private struct ImageSignature: Equatable {
    let width: Int
    let height: Int
    let bytes: Data

    init?(data: Data) {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        self.init(image: image)
    }

    init?(contentsOf url: URL) throws {
        guard let data = try? Data(contentsOf: url), let signature = ImageSignature(data: data) else {
            return nil
        }
        self = signature
    }

    init?(image: CGImage) {
        let imageWidth = image.width
        let imageHeight = image.height
        let (pixelCount, pixelOverflow) = imageWidth.multipliedReportingOverflow(by: imageHeight)
        let (byteCount, byteOverflow) = pixelCount.multipliedReportingOverflow(by: 4)
        guard imageWidth > 0, imageHeight > 0, !pixelOverflow, !byteOverflow else { return nil }

        var rendered = false
        var pixels = Data(count: byteCount)
        pixels.withUnsafeMutableBytes { buffer in
            let colorSpace = CGColorSpaceCreateDeviceRGB()
            guard let context = CGContext(
                data: buffer.baseAddress, width: imageWidth, height: imageHeight,
                bitsPerComponent: 8, bytesPerRow: imageWidth * 4, space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
            ) else { return }
            context.draw(image, in: CGRect(x: 0, y: 0, width: imageWidth, height: imageHeight))
            rendered = true
        }
        guard rendered else { return nil }
        width = imageWidth
        height = imageHeight
        bytes = pixels
    }
}
