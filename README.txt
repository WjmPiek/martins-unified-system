MARTINS FRANCHISE MODULE PERMISSION ENFORCEMENT

This package makes the three Admin module switches authoritative:

- Claims Access
- Heat Map Access
- Attendance Access

When a switch is cleared for a franchise user:

- the module is hidden from that user's sidebar;
- its launch link is unavailable;
- opening its URL directly returns Access Denied.

Admin and Super Admin accounts retain access to all modules.

INSTALL

1. Stop the local Martins system.
2. Open PowerShell in this extracted package folder.
3. Run:

python install_module_permissions.py "C:\Users\WjmLabtop\OneDrive\SERVER\martins-unified-system\martins-funeral-system"

4. Open PowerShell in the Martins system folder and run:

python sync_module_access_roles.py

Do not add --grant-existing unless you intentionally want to give every existing
franchise account access to all three modules. The normal command preserves the
module choices already made by Admin.

5. Start the system again:

python run.py

6. Sign in as Admin, open Administration > Franchise Users, edit a franchise
   user, keep Franchise User selected, and select only the modules that account
   may use.

7. Sign out of the franchise account and sign in again after changing its module
   access. The sidebar and direct URL protection will then use the new choices.

The installer creates a timestamped backup inside the Martins system folder.
