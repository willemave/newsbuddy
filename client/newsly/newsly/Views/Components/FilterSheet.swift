//
//  FilterSheet.swift
//  newsly
//
//  Created by Assistant on 7/9/25.
//

import SwiftUI

struct FilterSheet: View {
    @Binding var selectedContentType: String
    @Binding var selectedDate: String
    @Binding var selectedReadFilter: String
    @Environment(\.dismiss) private var dismiss
    
    let contentTypes: [String]
    let availableDates: [String]
    
    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                Form {
                    // Content Type Section
                    Section(header: Text("Content Type")) {
                        Picker("Content Type", selection: $selectedContentType) {
                            Text("All Types").tag("all")
                            ForEach(contentTypes, id: \.self) { type in
                                Text(type.replacingOccurrences(of: "_", with: " ").capitalized)
                                    .tag(type)
                            }
                        }
                        .pickerStyle(InlinePickerStyle())
                    }
                    
                    // Date Section
                    Section(header: Text("Date")) {
                        Picker("Date", selection: $selectedDate) {
                            Text("All Dates").tag("")
                            ForEach(availableDates, id: \.self) { date in
                                Text(formatDate(date)).tag(date)
                            }
                        }
                        .pickerStyle(InlinePickerStyle())
                    }
                    
                    // Read Status Section
                    Section(header: Text("Read Status")) {
                        Picker("Read Status", selection: $selectedReadFilter) {
                            Text("Unread Only").tag("unread")
                            Text("All Content").tag("all")
                            Text("Read Only").tag("read")
                        }
                        .pickerStyle(SegmentedPickerStyle())
                    }
                    
                    // Settings Section
                    Section {
                        NavigationLink(destination: SettingsView()) {
                            HStack {
                                Image(systemName: "gear")
                                    .foregroundColor(.accentColor)
                                Text("Settings")
                            }
                        }
                    }
                }
            }
            .navigationTitle("Filters")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                }
            }
        }
    }
    
    private func formatDate(_ dateString: String) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        
        guard let date = formatter.date(from: dateString) else {
            return dateString
        }
        
        let displayFormatter = DateFormatter()
        displayFormatter.dateStyle = .medium
        displayFormatter.timeStyle = .none
        
        return displayFormatter.string(from: date)
    }
}
