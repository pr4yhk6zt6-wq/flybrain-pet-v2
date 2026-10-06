//
//  FlyBrainPetApp.swift
//  FlyBrainPet
//
//  App entry — hosts the two modes (Life / Connectome) over ONE shared
//  simulation (spec #32, #119).
//

import SwiftUI

@main
struct FlyBrainPetApp: App {
    @StateObject private var appModel = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(appModel)
                .preferredColorScheme(.dark)
        }
    }
}