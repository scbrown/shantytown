"""Producer adapter for the shared notifier, never a second message sender."""
from .answer import Answer


class SpecReader:
    def __init__(self, tracker):
        self.tracker = tracker

    def events(self):
        value = self.tracker._spec_json("spec-events")
        events = value.get("events")
        if not isinstance(events, list) or len(events) > 500:
            raise ValueError("spec events incomplete or capped")
        result = []
        seen = set()
        for event in events:
            identifier, version = event.get("id"), event.get("version")
            if (not isinstance(identifier, str) or not identifier or type(version) is not int
                    or version < 1 or (identifier,version) in seen):
                raise ValueError("invalid spec event identity")
            seen.add((identifier,version))
            if event.get("receipt") is not None:
                raise ValueError("pending spec event already has a receipt")
            result.append({"kind":"spec", "id":identifier,"episode":str(version),
                           "recipient":event.get("recipient"),"version":version,
                           "previous":event.get("previous_version"),"summary":event.get("summary"),
                           "urgent":event.get("urgent")})
        return Answer.complete_read(result, how="selected Seeds retained spec intents")

    def ack(self, identifier, episode, receipt, *, attempted=False):
        value = self.tracker._spec_json("spec-receipt", identifier, episode).get("event", {})
        if value.get("id") != identifier or str(value.get("version")) != episode:
            raise ValueError("spec receipt answered a different generation")
        if "receipt" not in value:
            raise ValueError("spec receipt field UNKNOWN")
        current = value["receipt"]
        if current == receipt:
            return True
        if current is not None:
            raise ValueError("spec receipt conflicts")
        self.tracker._spec_json("spec-receipt-ack", identifier, episode, "--receipt", receipt)
        check = self.tracker._spec_json("spec-receipt", identifier, episode).get("event", {})
        return check.get("id") == identifier and str(check.get("version")) == episode and check.get("receipt") == receipt
