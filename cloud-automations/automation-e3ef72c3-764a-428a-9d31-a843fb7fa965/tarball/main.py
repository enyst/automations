import os,json
from openhands.sdk import Conversation
from openhands.tools.preset.default import get_default_agent
from openhands.workspace import OpenHandsCloudWorkspace

marker="TRANSPILE_NATIVE_AUTH_OK"
with OpenHandsCloudWorkspace(local_agent_server_mode=True, cloud_api_url=os.environ["OPENHANDS_CLOUD_API_URL"], cloud_api_key=os.environ["OPENHANDS_API_KEY"], keep_alive=True) as workspace:
    llm=workspace.get_llm(profile_name=os.environ["AUTOMATION_MODEL"])
    assert llm.model == "deepseek/deepseek-v4-pro", "Wrong model resolved"
    assert (llm.base_url or "").rstrip("/") == "https://api.deepseek.com", "Wrong endpoint resolved"
    assert llm.api_key is not None, "Missing profile credential"
    print("Profile resolved independently:",llm.model,llm.base_url,flush=True)
    llm=llm.model_copy(update={"max_output_tokens":4096,"num_retries":1})
    seen=[]
    conversation=Conversation(agent=get_default_agent(llm=llm,cli_mode=True),workspace=workspace,callbacks=[seen.append],max_iteration_per_run=6,delete_on_close=False)
    try:
        conversation.send_message("Authentication smoke only. Run exactly printf 'TRANSPILE_NATIVE_AUTH_OK\\n' in the terminal tool, then finish with that same token. Do not read files, access the network, or modify anything.")
        conversation.run()
        observations=[e for e in seen if type(e).__name__=="ObservationEvent" and getattr(e,"tool_name",None)=="terminal"]
        assert any(marker in json.dumps(e.model_dump(mode="json")) for e in observations), "No verified terminal output"
        print("SMOKE_OK",str(conversation.id),"terminal_observations",len(observations),flush=True)
    finally:
        conversation.close()
