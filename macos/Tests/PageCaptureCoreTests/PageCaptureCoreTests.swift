import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import PageCaptureCore

final class PageCaptureCoreTests: XCTestCase {
    private func png(
        _ color: (UInt8, UInt8, UInt8),
        width: Int = 3,
        height: Int = 2,
        changedPixel: (index: Int, color: (UInt8, UInt8, UInt8))? = nil
    ) -> Data {
        var values = Array(repeating: color.0, count: width * height * 4)
        for index in stride(from: 0, to: values.count, by: 4) {
            values[index + 1] = color.1; values[index + 2] = color.2; values[index + 3] = 255
        }
        if let changedPixel {
            let offset = changedPixel.index * 4
            values[offset] = changedPixel.color.0
            values[offset + 1] = changedPixel.color.1
            values[offset + 2] = changedPixel.color.2
        }
        let space = CGColorSpaceCreateDeviceRGB()
        let context = CGContext(data: &values, width: width, height: height, bitsPerComponent: 8,
                                bytesPerRow: width * 4, space: space,
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        let image = context.makeImage()!
        let output = NSMutableData()
        let destination = CGImageDestinationCreateWithData(output, "public.png" as CFString, 1, nil)!
        CGImageDestinationAddImage(destination, image, nil)
        CGImageDestinationFinalize(destination)
        return output as Data
    }

    private func session(_ name: String = #function) throws -> PageCaptureSession {
        try PageCaptureSession(root: URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(name))
    }

    func testOrderingAndExactDuplicateDecisions() throws {
        let s = try session()
        let first = try s.addPNG(png((255, 0, 0)))
        let second = try s.addPNG(png((0, 255, 0)))
        XCTAssertEqual([first.number, second.number], [1, 2])
        XCTAssertThrowsError(try s.addPNG(png((0, 255, 0)))) { XCTAssertEqual($0 as? PageCaptureError, .duplicate) }
        XCTAssertNoThrow(try s.addPNG(png((0, 255, 0), changedPixel: (0, (0, 255, 1)))))
        XCTAssertEqual(s.pageCount, 3)
    }

    func testOpeningGappedSessionDoesNotOverwriteExistingPage() throws {
        let s = try session()
        _ = try s.addPNG(png((1, 0, 0)))
        _ = try s.addPNG(png((2, 0, 0)))
        _ = try s.addPNG(png((3, 0, 0)))
        try FileManager.default.removeItem(at: s.pages[1].url)

        let reopened = try PageCaptureSession.open(directory: s.directory)
        let existingThirdPage = reopened.pages.last!.url
        let existingThirdPageData = try Data(contentsOf: existingThirdPage)
        let newPage = try reopened.addPNG(png((4, 0, 0)))

        XCTAssertEqual(reopened.pages.map(\.number), [1, 3, 4])
        XCTAssertEqual(newPage.number, 4)
        XCTAssertEqual(try Data(contentsOf: existingThirdPage), existingThirdPageData)
    }

    func testUndoMovesLatestIntoRecoverableDirectory() throws {
        let s = try session()
        _ = try s.addPNG(png((1, 2, 3)))
        let latest = try s.undoLatest()
        XCTAssertEqual(s.pageCount, 0)
        XCTAssertEqual(latest?.url.deletingLastPathComponent().lastPathComponent, "Undone")
        XCTAssertTrue(FileManager.default.fileExists(atPath: latest!.url.path))
    }

    func testSessionsAreUniqueAtSameTimestamp() throws {
        let root = URL(fileURLWithPath: NSTemporaryDirectory()).appendingPathComponent(#function)
        let date = Date(timeIntervalSince1970: 0)
        let a = try PageCaptureSession(root: root, date: date)
        let b = try PageCaptureSession(root: root, date: date)
        XCTAssertNotEqual(a.directory, b.directory)
    }

    func testExportPreconditionsAndAtomicDestination() throws {
        let s = try session()
        let output = s.directory.deletingLastPathComponent().appendingPathComponent("result.pdf")
        XCTAssertThrowsError(try s.exportPDF(to: output)) { XCTAssertEqual($0 as? PageCaptureError, .noPages) }
        _ = try s.addPNG(png((10, 20, 30)))
        try Data("old".utf8).write(to: output)
        try s.exportPDF(to: output)
        XCTAssertNotEqual(try Data(contentsOf: output), Data("old".utf8))
        XCTAssertEqual(CGPDFDocument(CGDataProvider(filename: output.path)!)?.numberOfPages, 1)
    }
}
