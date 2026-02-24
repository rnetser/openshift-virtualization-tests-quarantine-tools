"""Constants for quarantine tools."""

# Quarantine marker string used in xfail reasons
QUARANTINED = "quarantined"

# Team directory mappings (test directory → team name)
TEAM_DIRECTORIES = {
    "chaos": "chaos",
    "virt": "virt",
    "network": "network",
    "storage": "storage",
    "install_upgrade_operators": "iuo",
    "observability": "observability",
    "infrastructure": "infrastructure",
    "data_protection": "data_protection",
    "compute": "virt",
    "cross_cluster_live_migration": "storage",
}
