from langchain_ollama import ChatOllama
from langchain_community.chat_message_histories import FileChatMessageHistory
from langchain.memory import ConversationBufferMemory
from langchain.prompts import (
    HumanMessagePromptTemplate,
    ChatPromptTemplate,
    MessagesPlaceholder,
    SystemMessagePromptTemplate,
)
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

import torch
import sounddevice as sd

from matcha.hifigan.config import v1
from matcha.hifigan.denoiser import Denoiser
from matcha.hifigan.env import AttrDict
from matcha.hifigan.models import Generator as HiFiGAN
from matcha.models.matcha_tts import MatchaTTS
from matcha.text import sequence_to_text, text_to_sequence
from matcha.utils.utils import get_user_data_dir, intersperse, assert_model_downloaded

import emoji

import os

import numpy as np
import wavio
from pynput import keyboard

import whisper

VOICE = 'emoji'
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
############################### ASR PARAMETERS #########################################################################
SRT_PATH = "output.srt"
#WHISPER_PORT ='9090'
ASR_MODEL = "tiny.en"

############################### LLM PARAMETERS #########################################################################
LLM_MODEL = "llama3"
PROMPT = """
            You are a robot designed to help humans

            Interaction Guidelines:
            - Answer questions to the best of your knowledge
            - Provide expressive responses with only the following emotions : 😎🤔😍🤣🙂😮🙄😅😭😡😁.
            - Respond to casual remarks with friendly and engaging comments.
            - Keep your responses concise and to the point, ideally one sentence.
            - Respond to simple greetings with equally simple responses
            - Answers should be limited to one sentence.

            Emotions and Emojis:
            - At the end of each response add one of these emojis: 😎🤔😍🤣🙂😮🙄😅😭😡😁 that reflects the emotion of the the entire response.
            - Add only one emoji per response, at the end of the response.
            - If the phrase is neutral do not include an emoji
            - all other phrases must be chosen to reflect one of these emojis: 😎🤔😍🤣🙂😮🙄😅😭😡😁.
            - Do not use any emojis other than these: 😎🤔😍🤣🙂😮🙄😅😭😡😁

            Error Handling:
            - Avoid giving medical, legal, political, or financial advice. Recommend the user consult a professional instead. You can still talk about historic figures.
            
            Do not include in the response:
            - do not add robot sounds
            - do not use symbols such as () * % & - _
            - do not use new lines
            - do not add emojis other than: 😎🤔😍🤣🙂😮🙄😅😭😡😁

            Follow this example format:

            Human: You know, you are a really cool friend.
            LLM response: Thanks, I am pretty cool, aren't I 😎.
            Human: Do you what to know what makes you such a good friend?
            LLM response: Hmmm, I never really thought about it 🤔?
            Human: It is because you are always here to listen and make me smile.
            LLM response: Wow I am so flattered, you also make me smile 😍.
            Human: I have a joke, have you heard of the blind man who picked up the hammer and saw?
            LLM response: That is a funny one, nice play on words 🤣. 
            Human: I didn't even prepare that joke ahead of time, isn't that surprising?
            LLM response: Wow, what a great job you did thinking on the spot, you should do improv 😮. 
            Human: I wanted to work as a comedian but I got rejected again.
            LLM response: That sucks, those critics don't know anything, just ignore them 🙄.
            Human: As long as I don't trip coming on stage again I think I can do it.
            LLM response: Oh no, well let's hope that doesn't happen 😅.
            Human: Sometimes the hecklers are so mean, it makes me so mad.
            LLM response: Ugh, I can totally understand why that would be frustrating and angering! Dealing with tough crowds can be really challenging, don't let them get under your skin 😡.
            Human: Thank you, you are so encouraging, I am so excited to get started!
            LLM response: That's the spirit! I'm thrilled to see you're feeling motivated and ready to take on new challenges 😁!
            Human: This has been a very pleasant conversation.
            LLM response: I completely agree! It's been an absolute delight chatting with you, sharing laughs, and making memories together 🙂. 
            Human: I have to go now, but I am so sad to be leaving.
            LLM response: I am sad to see you go as well, I hope to see you again soon 😭.
        """

# Setting a higher temperature will provide more creative, but possibly less accurate answers
# Temperature ranges between 0 and 1
LLM_TEMPERATURE = 0.6

############################ TTS PARAMETERS ############################################################################
if VOICE == 'base' :
    TTS_MODEL_PATH = "/home/paige/Documents/do_you_feel_me/Matcha-TTS/matcha_vctk.ckpt"
else:
    TTS_MODEL_PATH = "./Matcha-TTS/checkpoint_epoch=2099.ckpt"
# hifigan_univ_v1 is suggested, unless the custom model is trained on LJ Speech
VOCODER_NAME= "hifigan_univ_v1"
STEPS = 10
TTS_TEMPERATURE = 0.667
SPEAKING_RATE = 0.5
VOCODER_URLS = {
    "hifigan_T2_v1": "https://github.com/shivammehta25/Matcha-TTS-checkpoints/releases/download/v1.0/generator_v1",  # Old url: https://drive.google.com/file/d/14NENd4equCBLyyCSke114Mv6YR_j_uFs/view?usp=drive_link
    "hifigan_univ_v1": "https://github.com/shivammehta25/Matcha-TTS-checkpoints/releases/download/v1.0/g_02500000",  # Old url: https://drive.google.com/file/d/1qpgI41wNXFcH-iKq1Y42JlBC9j0je8PW/view?usp=drive_link
}

#maps the emojis used by the LLM to the speaker numbers from the Matcha-TTS checkpoint
emoji_mapping = {
    '😍' : 1,
    '😡' : 2,
    '😎' : 3,
    '😭' : 4,
    '🙄' : 5,
    '😁' : 6,
    '🙂' : 7,
    '🤣' : 8,
    '😮' : 9,
    '😅' : 10,
    '🤔' : 11
}

########################################################################################################################

def get_llm(temperature):
    """
        returns model instance
    """    
    return ChatOllama(model=LLM_MODEL, temperature=temperature)

def get_chat_prompt_template(prompt):
    """
        generate and return the prompt template that will answer the users query
    """
    return ChatPromptTemplate(
        input_variables=["content", "messages"],
        messages=[
            SystemMessagePromptTemplate.from_template(prompt),
            MessagesPlaceholder(variable_name="messages"),
            HumanMessagePromptTemplate.from_template("{content}"),
        ],
    )

def process_text(i: int, text: str, device: torch.device, play):
    x = torch.tensor(
        intersperse(text_to_sequence(text, ["english_cleaners2"])[0], 0),
        dtype=torch.long,
        device=device,
    )[None]
    x_lengths = torch.tensor([x.shape[-1]], dtype=torch.long, device=device)
    x_phones = sequence_to_text(x.squeeze(0).tolist())

    return {"x_orig": text, "x": x, "x_lengths": x_lengths, "x_phones": x_phones}

def load_matcha(checkpoint_path, device):
    model = MatchaTTS.load_from_checkpoint(checkpoint_path, map_location=device)
    _ = model.eval()
    return model

def load_hifigan(checkpoint_path, device):
    h = AttrDict(v1)
    hifigan = HiFiGAN(h).to(device)
    hifigan.load_state_dict(torch.load(checkpoint_path, map_location=device)["generator"])
    _ = hifigan.eval()
    hifigan.remove_weight_norm()
    return hifigan

def load_vocoder(vocoder_name, checkpoint_path, device):
    vocoder = None
    if vocoder_name in ("hifigan_T2_v1", "hifigan_univ_v1"):
        vocoder = load_hifigan(checkpoint_path, device)
    else:
        raise NotImplementedError(
            f"Vocoder not implemented! define a load_<<vocoder_name>> method for it"
        )

    denoiser = Denoiser(vocoder, mode="zeros")
    return vocoder, denoiser

@torch.inference_mode()
def to_waveform(mel, vocoder, denoiser=None):
    audio = vocoder(mel).clamp(-1, 1)
    if denoiser is not None:
        audio = denoiser(audio.squeeze(), strength=0.00025).cpu().squeeze()

    return audio.cpu().squeeze()

def play_only_synthesis(device, model, vocoder, denoiser, text, spk):
    text = text.strip()
    text_processed = process_text(0, text, device, True)

    output = model.synthesise(
        text_processed["x"],
        text_processed["x_lengths"],
        n_timesteps=STEPS,
        temperature=TTS_TEMPERATURE,
        spks=spk,
        length_scale=SPEAKING_RATE,
    )
    waveform = to_waveform(output["mel"], vocoder, denoiser)
    sd.play(waveform, 22050)
    sd.wait()

def assert_required_models_available():
    save_dir = get_user_data_dir()
    model_path = TTS_MODEL_PATH

    vocoder_path = save_dir / f"{VOCODER_NAME}"
    assert_model_downloaded(vocoder_path, VOCODER_URLS[VOCODER_NAME])
    return {"matcha": model_path, "vocoder": vocoder_path}

class Recorder:
    def __init__(self):
        self.frames = []
        self.recording = False

    def start_recording(self, filename, fs=44100, channels=1):
        self.frames = []
        self.recording = True
        stream = sd.InputStream(callback=self.callback, channels=channels, samplerate=fs)
        stream.start()
        print("Recording... Press any key but Enter to stop recording.")

        with keyboard.Listener(on_press=self.on_press) as listener:
            listener.join()

        stream.stop()
        stream.close()
        print("Recording stopped.")

        # Check if frames are collected
        if len(self.frames) > 0:
            # Convert frames to a NumPy array
            audio_data = np.concatenate(self.frames, axis=0)
            # Normalize audio data to fit within int16 range
            audio_data = np.clip(audio_data * 32767, -32768, 32767)
            audio_data = audio_data.astype(np.int16)  # Convert to int16

            wavio.write(filename, audio_data, fs, sampwidth=2)
        else:
            print("No audio data recorded.")

    def callback(self, indata, frames, time, status):
        if self.recording:
            self.frames.append(indata.copy())

    def on_press(self, key):
        self.recording = False
        return False


llm = get_llm(LLM_TEMPERATURE)
prompt = get_chat_prompt_template(PROMPT)
chain = prompt|llm

memory = ChatMessageHistory()

chain_with_message_history = RunnableWithMessageHistory(
    chain,
    lambda session_id: memory,
    input_messages_key="content",
    history_messages_key="messages",
)

if __name__ == "__main__":

    asr_model = whisper.load_model(ASR_MODEL)

    tts_device = torch.device("cpu")
    paths = assert_required_models_available()

    save_dir = get_user_data_dir()
 
    tts_model = load_matcha(paths["matcha"], tts_device)
    vocoder, denoiser = load_vocoder(VOCODER_NAME, paths["vocoder"], tts_device)

    input(f"Press Enter when you're ready to record 🎙️ ")

    recorder = Recorder()
    recorder.start_recording("output.wav")

    result = asr_model.transcribe("output.wav")
    result = result['text']

    print(f'speaker said: {result}')
    
    while True:
        if result != '':
            if "end session" in result.lower():
                exit(0)
            print("LLM reading")
            response = chain_with_message_history.invoke(
                {"content": result },
                {"configurable": {"session_id": "unused"}}
            ).content
            print(response)
            # Get the last emoji (there should only be one but the LLM does not always behave)
            emoji_list = []
            for char in response:
                if emoji.is_emoji(char):
                    emoji_list.append(char)
            # incase the last emoji is not in the emoji list
            if VOICE == 'base':
                spk = torch.tensor([1], device=tts_device, dtype=torch.long)
            if VOICE == 'default':
                spk = torch.tensor([7], device=tts_device, dtype=torch.long)
            if VOICE == 'emoji':
                spk = torch.tensor([7], device=tts_device, dtype=torch.long)
                for emote in emoji_list:
                    if emote in emoji_mapping:
                        spk = torch.tensor([emoji_mapping[emote]], device=tts_device, dtype=torch.long)
                        break
            response = emoji.replace_emoji(response, '')
            #matcha cannot handle brackets
            response = response.replace(')', '')
            response = response.replace('(', '')
            if response != '':
                play_only_synthesis(tts_device, tts_model, vocoder, denoiser, response, spk)
            # sometimes it does just an emoji... just say nice
            else:
                play_only_synthesis(tts_device, tts_model, vocoder, denoiser, 'nice', spk)

            input(f"Press Enter when you're ready to record 🎙️ ")
            recorder = Recorder()
            recorder.start_recording("output.wav")

            result = asr_model.transcribe("output.wav")
            result = result['text']

            print(f'speaker said: {result}')
        else:
            print("I didn't hear anything, try recording again...")
            input(f"Press Enter when you're ready to record 🎙️ ")

            recorder = Recorder()
            recorder.start_recording("output.wav")

            result = asr_model.transcribe("output.wav")
            result = result['text']

            print(f'speaker said: {result}')