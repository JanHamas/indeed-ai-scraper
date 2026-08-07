FROM apify/actor-python:3.14

USER myuser

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && echo "All installed Python packages:" \
 && pip freeze

# ── Bake the AI-matching model into the image at build time, so runs
# never pay a cold-download cost from Hugging Face Hub. Apify containers
# are ephemeral — nothing persists between runs — so without this,
# SemanticMatcher.__init__ re-downloads JobBERT-v2 on every single run,
# which is the ~70s startup delay. Env vars keep the build log quiet and
# fix a version-mismatch deprecation warning at the same time.
ENV HF_HUB_DISABLE_PROGRESS_BARS=1 \
    TRANSFORMERS_VERBOSITY=error \
    TOKENIZERS_PARALLELISM=false

RUN echo "Pre-downloading AI matching model into image layer:" \
 && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('TechWolf/JobBERT-v2')" \
 && echo "Model cached."

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]