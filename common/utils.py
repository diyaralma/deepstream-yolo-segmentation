import ctypes
import sys
import os
from collections import namedtuple
import random

sys.path.append('/opt/nvidia/deepstream/deepstream/lib')

# ANSI escape codes for colors
RED = "\033[91m"  # Red for error messages
YELLOW = "\033[93m"  # Yellow for warning messages
RESET = "\033[0m"  # Reset color
GREEN = "\033[92m"  # Green for success messages

ColorObject = namedtuple("ColorObject", ["red", "green", "blue", "alpha"])


def long_to_uint64(l):
    value = ctypes.c_uint64(l & 0xffffffffffffffff).value
    return value

def clear_screen():
    os.system('clear')

       
def display_message(msg_type="d", message=None):
    if msg_type == "e":  # Error
        print(f"{RED}Error: {message}{RESET}")
    elif msg_type == "w":  # Warning
        print(f"{YELLOW}Warning: {message}{RESET}")
    elif msg_type == "s":  # Success
        print(f"{GREEN}Success: {message}{RESET}")
    else:
        print(message)  # Default case without color



def create_dynamic_labels(config_path):
    """Create dynamic colors for labels based on the label file path specified in the config."""
    
    # Load label file path from the configuration
    label_file_path = None
    with open(config_path, 'r') as file:
        for line in file:
            if line.startswith("labelfile-path="):
                label_file_path = line.strip().split('=')[1]
                break

    # Load labels from the label file
    labels = []
    with open(label_file_path, 'r') as file:
        labels = [line.strip() for line in file if line.strip()]

    # Generate random colors for each label ID (index)
    dynamic_labels = {}
    for idx, label in enumerate(labels):
        dynamic_labels[idx] = ColorObject(
            red=random.random(),
            green=random.random(),
            blue=random.random(),
            alpha=1.0  # Alpha is set to 1 for full opacity
        )

    return dynamic_labels
