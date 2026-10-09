"""Built-in workflow recipes (read-only). Copy one under another name to change it."""

RECIPES = {
    # K3: the snapshot-before-upgrade recipe
    "safe-upgrade": '''\
name = "Safe upgrade"
description = "Snapshot the database and filestore, upgrade the modules, then run their tests in a throwaway database"

[params.config]
kind = "config"
description = "Odoo config of the instance"

[params.database]
kind = "database"
description = "Database to upgrade"

[params.modules]
kind = "modules"
description = "Modules to upgrade"

[[steps]]
op = "db.snapshot"
title = "Snapshot {database}"
config = "{config}"
database = "{database}"

[[steps]]
op = "modules.upgrade"
title = "Upgrade {modules} in {database}"
config = "{config}"
database = "{database}"
modules = "{modules}"
snapshot = false

[[steps]]
op = "modules.test"
title = "Test {modules}"
config = "{config}"
modules = "{modules}"
''',
    "pull-and-upgrade": '''\
name = "Pull and upgrade"
description = "Pull the installation's repositories (fast-forward only), snapshot, upgrade the modules, start the instance"

[params.config]
kind = "config"
description = "Odoo config of the instance"

[params.database]
kind = "database"

[params.modules]
kind = "modules"

[[steps]]
op = "instance.stop"
config = "{config}"

[[steps]]
op = "git.pull"
installation = "{installation}"

[[steps]]
op = "db.snapshot"
config = "{config}"
database = "{database}"

[[steps]]
op = "modules.upgrade"
config = "{config}"
database = "{database}"
modules = "{modules}"
snapshot = false

[[steps]]
op = "instance.start"
config = "{config}"
database = "{database}"
''',
    "test-copy": '''\
name = "Neutralized test copy"
description = "Clone a database with its filestore, neutralize the copy, start the instance on it"

[params.config]
kind = "config"

[params.database]
kind = "database"
description = "Database to copy (it is only read)"

[params.target]
kind = "new_database"
description = "Name of the copy"

[[steps]]
op = "db.clone"
config = "{config}"
database = "{database}"
target = "{target}"

[[steps]]
op = "db.neutralize"
config = "{config}"
database = "{target}"
auto = true

[[steps]]
op = "instance.start"
config = "{config}"
database = "{target}"
''',
}
