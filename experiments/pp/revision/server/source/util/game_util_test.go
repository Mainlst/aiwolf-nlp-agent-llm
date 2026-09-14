package util

import (
	"testing"

	"github.com/aiwolfdial/aiwolf-nlp-server/model"
	"github.com/stretchr/testify/require"
)

func TestOrderedRoleConnectionsUsesOptimizerSeats(t *testing.T) {
	roleMap := map[model.Role][]model.Connection{
		model.R_POSSESSED: {{TeamName: "target"}},
		model.R_WEREWOLF:  {{TeamName: "wolf"}},
		model.R_SEER:      {{TeamName: "seer"}},
		model.R_VILLAGER:  {{TeamName: "villager-b"}, {TeamName: "villager-a"}},
	}
	teamIndexes := map[string]int{
		"villager-a": 0,
		"seer":       1,
		"target":     2,
		"wolf":       3,
		"villager-b": 4,
	}

	ordered := orderedRoleConnections(roleMap, teamIndexes)
	teams := make([]string, len(ordered))
	roles := make([]model.Role, len(ordered))
	for index, item := range ordered {
		teams[index] = item.conn.TeamName
		roles[index] = item.role
	}

	require.Equal(t, []string{"villager-a", "seer", "target", "wolf", "villager-b"}, teams)
	require.Equal(
		t,
		[]model.Role{model.R_VILLAGER, model.R_SEER, model.R_POSSESSED, model.R_WEREWOLF, model.R_VILLAGER},
		roles,
	)
}
