// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "PageCaptureMac",
    platforms: [.macOS(.v14)],
    products: [
        .library(name: "PageCaptureCore", targets: ["PageCaptureCore"]),
        .executable(name: "PageCaptureMac", targets: ["PageCaptureMac"])
    ],
    targets: [
        .target(name: "PageCaptureCore"),
        .executableTarget(name: "PageCaptureMac", dependencies: ["PageCaptureCore"]),
        .testTarget(name: "PageCaptureCoreTests", dependencies: ["PageCaptureCore"])
    ]
)
