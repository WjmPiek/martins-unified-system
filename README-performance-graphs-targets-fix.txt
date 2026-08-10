MARTINS PERFORMANCE GRAPHS AND TARGETS REPAIR

This repair fixes two things:
1. Performance graphs rebuild and store their cache automatically when the figures exist.
2. Only Admin and Super Admin can edit targets. Franchise users can view their own graphs only.

INSTALL
1. Stop the local Martins system with Ctrl+C.
2. Extract this ZIP into:
   C:\Users\WjmLabtop\OneDrive\SERVER\martins-unified-system\martins-funeral-system
3. In that folder, run:
   python install_performance_graphs_fix.py
4. Start the system again:
   python run.py

The first visit to a graph for a new month may take a short moment while its cache is created. Later visits use the saved cache.

The installer creates a dated backup folder before changing anything.
