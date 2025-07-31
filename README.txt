Im VZ wo das Dockerfile liegt:

docker build -t au_beleg .

Wenn build OK, dann

docker run -p 8000:8000 au_beleg

--> "NAME_DINGS" läuft dann, wenn Docker Desktop läuft, ist build + run einmal im Terminal notwendig damit er dort aufscheint.

----->>> WENN ÄNDERUNGEN IM CODE IMMER DOCKER NEU BUILDEN!

