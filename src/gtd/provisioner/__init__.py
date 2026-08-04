"""Provisioner: alta y baja de credenciales de panel en el broker.

Proceso APARTE del GtD (`python -m gtd.provisioner`), con privilegios propios.
Comparte este repo porque la derivación HMAC tiene que coincidir byte a byte con
el firmware; no comparte el proceso porque el GtD está deliberadamente encerrado
y esto necesita escribir /etc/mosquitto y recargar el broker.
"""
