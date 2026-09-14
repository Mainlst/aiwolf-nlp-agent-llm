package model

import "encoding/json"

type Info struct {
	GameID         string           `json:"game_id"`
	Day            int              `json:"day"`
	Agent          *Agent           `json:"agent"`
	Profile        *string          `json:"profile,omitempty"`
	MediumResult   *Judge           `json:"medium_result,omitempty"`
	DivineResult   *Judge           `json:"divine_result,omitempty"`
	ExecutedAgent  *Agent           `json:"executed_agent,omitempty"`
	AttackedAgent  *Agent           `json:"attacked_agent,omitempty"`
	VoteList       []Vote           `json:"vote_list,omitempty"`
	AttackVoteList []Vote           `json:"attack_vote_list,omitempty"`
	TalkList       []Talk           `json:"-"`
	WhisperList    []Talk           `json:"-"`
	StatusMap      map[Agent]Status `json:"status_map"`
	RoleMap        map[Agent]Role   `json:"role_map"`
	RemainCount    *int             `json:"remain_count,omitempty"`
	RemainLength   *int             `json:"remain_length,omitempty"`
	RemainSkip     *int             `json:"remain_skip,omitempty"`
}

func (i Info) MarshalJSON() ([]byte, error) {
	statusMap := make(map[string]Status)
	for k, v := range i.StatusMap {
		statusMap[k.String()] = v
	}
	roleMap := make(map[string]Role)
	for k, v := range i.RoleMap {
		roleMap[k.String()] = v
	}
	type Alias Info
	return json.Marshal(&struct {
		*Alias
		StatusMap map[string]Status `json:"status_map"`
		RoleMap   map[string]Role   `json:"role_map"`
	}{
		Alias:     (*Alias)(&i),
		StatusMap: statusMap,
		RoleMap:   roleMap,
	})
}
