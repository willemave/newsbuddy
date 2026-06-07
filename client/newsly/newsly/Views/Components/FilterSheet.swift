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
    private let readStatusOptions = [
        FormChoiceOption(title: "Unread", value: "unread"),
        FormChoiceOption(title: "All", value: "all"),
        FormChoiceOption(title: "Read", value: "read"),
    ]
    
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
                        .labelsHidden()
                    }
                    
                    // Date Section
                    Section(header: Text("Date")) {
                        Menu {
                            Button("All Dates") {
                                selectedDate = ""
                            }
                            ForEach(availableDates, id: \.self) { date in
                                Button(formatDate(date)) {
                                    selectedDate = date
                                }
                            }
                        } label: {
                            HStack {
                                Text(selectedDateTitle)
                                    .foregroundStyle(Color.onSurface)
                                Spacer()
                                Image(systemName: "chevron.up.chevron.down")
                                    .font(.appSymbol(size: 13, weight: .semibold))
                                    .foregroundStyle(Color.onSurfaceSecondary)
                                    .accessibilityHidden(true)
                            }
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        }
                        .accessibilityLabel("Date")
                        .accessibilityValue(selectedDateTitle)
                    }
                    
                    // Read Status Section
                    Section(header: Text("Read Status")) {
                        FormChoicePillGroup(
                            options: readStatusOptions,
                            selection: $selectedReadFilter,
                            unselectedBackground: .surfaceContainer
                        )
                        .padding(.vertical, 4)
                    }
                    
                    // Settings Section
                    Section {
                        NavigationLink(destination: SettingsView()) {
                            HStack {
                                Image(systemName: "gear")
                                    .foregroundColor(.brandPrimary)
                                    .accessibilityHidden(true)
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

    private var selectedDateTitle: String {
        selectedDate.isEmpty ? "All Dates" : formatDate(selectedDate)
    }
}
