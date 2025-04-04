# Big Video Converter Development Documentation

This document provides information for developers working on the Big Converter application. It describes the architecture, file structure, and individual components of the codebase.

## Architecture Overview

Big Converter is a GTK4/Adwaita application for converting video files, built using Python. The application follows a modular architecture with the following key components:

- Main application window with tabbed interface (single file, batch conversion, settings)
- Settings persistence using GSettings
- Internationalization support
- Progress tracking for conversion processes

## File Structure and Components

### Main Application

- **big-video-converter.py**: Entry point for the application. Defines the `VideoConverterApp` class which inherits from `Adw.Application` and sets up the main window with tabs for different conversion modes. Handles application initialization, window creation, and UI setup.

### Core Components

- **constants.py**: Contains application-wide constants including paths to executables, application metadata, default settings, and UI constants. Centralizes configuration parameters used across the application.

- **settings_manager.py**: Provides a wrapper around GSettings for persistent storage of application preferences. Handles loading and saving settings with appropriate type conversion and error handling.

- **conversion.py**: Implements the core functionality for executing video conversion processes and monitoring their progress. Contains utilities for running external commands with progress tracking.

### UI Components

- **batch_page.py**: Implements the batch conversion interface for processing multiple MKV files. Allows users to select a directory for scanning, set concurrent conversion limits, configure minimum file size thresholds for successful conversions, and manage logging options.

- **single_file_page.py**: Provides the UI for single-file conversion. Handles file selection, output configuration, and initiates conversion of individual video files.

- **settings_page.py**: Contains global application settings UI. Manages encoding options, GPU selection, video/audio quality settings, subtitle handling preferences, and other conversion parameters.

- **progress_dialog.py**: Implements a dialog for showing conversion progress. Handles displaying real-time feedback during conversion operations, including progress bars and status messages.

## Application Flow

1. The application starts in `big-video-converter.py`, creating the main window and tabs
2. User interacts with one of three interfaces:
   - Single file conversion
   - Batch conversion
   - Settings configuration
3. When conversion is initiated:
   - Settings are saved via `settings_manager.py`
   - Conversion process is launched through `conversion.py` 
   - Progress is displayed using `progress_dialog.py`

## Key Patterns

### Settings Management

The application uses GSettings for persistent configuration. The pattern is:
1. Define schema keys in the GSchema XML file
2. Access settings via the `settings_manager.py` wrapper
3. UI components load/save their state through this manager

### UI Construction

Each major UI component follows a common pattern:
1. Create a class that wraps the page functionality (e.g., `BatchPage`)
2. Initialize the UI elements in a constructor
3. Provide a `get_page()` method that returns the root widget
4. Connect signals to handler methods within the class

### Translation Support

Internationalization is implemented using gettext:
1. Text strings are wrapped with the `_()` function
2. Translations are loaded from `/usr/share/locale`
3. Each module imports and sets up translation independently

## Environment Variables

The conversion scripts recognize several environment variables that control the conversion behavior. These variables can be set programmatically in the `settings_page.py` implementation and are passed to the conversion processes.

## Command-Line Tools

The application wraps command-line tools:
- `/usr/bin/big-converter`: For single file conversion

## Error Handling

Error handling follows these patterns:
- UI feedback is provided through dialog boxes
- Conversion errors are captured from subprocess outputs
- Settings errors fall back to reasonable defaults
