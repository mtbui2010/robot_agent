"""A goal set that only walks somewhere, for a request whose English reading names an object to handle, is an
object issue (the model dropped the rest); a go-only goal for a request with no such object is fine."""

from robot_agent.core.planning.understand import vacuous_goal_issues

from test_fix_understand_goals import G, DS


def test_go_only_goals_with_an_object_in_the_reading_are_an_issue(places):
    issues = vacuous_goal_issues([G("go", "", "", DS)], "Could you put the scarf on the desk?", places)
    assert [i["kind"] for i in issues] == ["object"] and "scarf" in issues[0]["what"]


def test_go_only_goals_for_a_plain_go_request_have_no_issue(places):
    assert vacuous_goal_issues([G("go", "", "", DS)], "Please go to the desk.", places) == []


def test_a_goal_that_moves_something_is_not_checked_here(places):
    assert vacuous_goal_issues([G("on", "scarf", "", DS)], "Put the scarf on the desk.", places) == []


def test_no_english_reading_means_no_issue(places):
    assert vacuous_goal_issues([G("go", "", "", DS)], "", places) == []
