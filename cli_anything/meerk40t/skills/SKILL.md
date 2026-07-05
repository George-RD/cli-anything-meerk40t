---
name: "cli-anything-meerk40t"
description: "Command-line interface for Meerk40T - Agent CLI harness for **MeerK40t** laser cutting/engraving software."
---

# cli-anything-meerk40t

Agent CLI harness for **MeerK40t** laser cutting/engraving software.

## Installation

This CLI is installed as part of the cli-anything-meerk40t package:

```bash
pip install cli-anything-meerk40t
```

**Prerequisites:**
- Python 3.10+
- Meerk40T must be installed on your system

## Usage

### Basic Commands

```bash
# Show help
cli-anything-meerk40t --help

# Start interactive REPL mode
cli-anything-meerk40t

# Create a new project
cli-anything-meerk40t project new -o project.json

# Run with JSON output (for agent consumption)
cli-anything-meerk40t --json project info -p project.json
```

## Command Groups

### Cli

cli-anything-meerk40t — agent CLI for MeerK40t laser software.

| Command | Description |
|---------|-------------|
| `console` | Pass a raw command to the MeerK40t console. |
| `repl` | Run the interactive REPL. |

### Project

Project management (SVG files).

| Command | Description |
|---------|-------------|
| `new` | Create a new project. |
| `open` | Open an existing SVG project. |
| `save` | Save the current project to an SVG file. |
| `info` | Show project information. |
| `close` | Close the current project. |

### Elements

Element operations.

| Command | Description |
|---------|-------------|
| `circle` | Add a circle element. |
| `rect` | Add a rectangle element. |
| `ellipse` | Add an ellipse element. |
| `line` | Add a line element. |
| `polyline` | Add a polyline element (pairs of coordinates). |
| `text` | Add a text element. |
| `list` | List elements in the project. |
| `delete` | Delete an element by index. |
| `select` | Select an element by index. |
| `clear` | Clear all elements. |
| `frame` | Add a frame element. |
| `translate` | Translate an element. |
| `scale` | Scale an element. |
| `rotate` | Rotate an element. |
| `align` | Align elements. |
| `group` | Group elements. |
| `ungroup` | Ungroup elements. |

### Operations

Operation management.

| Command | Description |
|---------|-------------|
| `list` | List operations. |
| `add` | Add an operation (cut, engrave, raster, image, dots). |
| `classify` | Classify elements into operations. |
| `declassify` | Declassify elements from operations. |
| `set` | Set an operation property. |
| `delete` | Delete an operation by index. |
| `clear` | Clear all operations. |

### Device

Device control.

| Command | Description |
|---------|-------------|
| `list` | List devices. |
| `status` | Show device status. |
| `home` | Home the device. |
| `physical-home` | Perform physical home. |
| `move` | Move the device. |
| `info` | Show device information. |

### Export

Export project.

| Command | Description |
|---------|-------------|
| `svg` | Export project as SVG. |
| `svgz` | Export project as compressed SVGZ. |
| `png` | Export project as PNG (requires wxPython renderer). |
| `gcode` | Export project as G-code (best-effort). |

### Session

Session management.

| Command | Description |
|---------|-------------|
| `undo` | Undo the last command. |
| `redo` | Redo the last undone command. |
| `history` | Show command history. |
| `status` | Show session status. |

## Examples

### Create a New Project

Create a new meerk40t project file.

```bash
cli-anything-meerk40t project new -o myproject.json
# Or with JSON output for programmatic use
cli-anything-meerk40t --json project new -o myproject.json
```

### Interactive REPL Session

Start an interactive session with undo/redo support.

```bash
cli-anything-meerk40t
# Enter commands interactively
# Use 'help' to see available commands
# Use 'undo' and 'redo' for history navigation
```

### Export Project

Export the project to a final output format.

```bash
cli-anything-meerk40t --project myproject.json export render output.pdf --overwrite
```

## For AI Agents

When using this CLI programmatically:

1. **Always use `--json` flag** for parseable output
2. **Check return codes** - 0 for success, non-zero for errors
3. **Parse stderr** for error messages on failure
4. **Use absolute paths** for all file operations
5. **Verify outputs exist** after export operations

## Version

1.1.0