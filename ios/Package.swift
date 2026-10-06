// swift-tools-version:5.9
//
//  Package.swift
//  FlyBrain (SwiftPM manifest for the core engine)
//
//  Lets the simulation core be compiled & tested WITHOUT Xcode project files:
//  - `swift build`  (macOS or Linux)
//  - `swift test`   (runs the XCTest suite on any platform)
//
//  The iOS app shell (Phase 9-10) will be an Xcode project that depends on
//  this same source (either via SwiftPM or by direct file inclusion).
//
import PackageDescription

let package = Package(
    name: "FlyBrainCore",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
    ],
    products: [
        .library(name: "FlyBrainCore", targets: ["FlyBrainCore"]),
    ],
    targets: [
        .target(
            name: "FlyBrainCore",
            path: "Sources/FlyBrainCore",
            exclude: ["Network.hpp"]   // C++ stub unused by SwiftPM
        ),
        .testTarget(
            name: "FlyBrainCoreTests",
            dependencies: ["FlyBrainCore"],
            path: "Tests/FlyBrainCoreTests"
        ),
    ]
)