# Backups del SQLite de la lista de la compra

`backup.py` hace una copia consistente diaria de `shopping_list.sqlite` (via
`sqlite3.Connection.backup()`, seguro aunque el fichero esté en uso) y elimina las copias de
más de 14 días. Se lanza por cron en `vdiaz`, no dentro de un contenedor -- es un script suelto
sin dependencias nuevas (`sqlite3` es de la librería estándar).

Destino: `/mnt/storage/backups/shopping-list/` -- **mismo disco físico (`sda`) que la base de
datos origen** (`/mnt/storage/appdata/shopping-list/shopping_list.sqlite`), decisión consciente
de Victor. Protege de borrados/corrupción accidental del fichero o de un `podman rm` mal dado,
pero **no** de un fallo del propio disco `sda` -- si se quiere esa protección más adelante, el
destino tendría que ser `/mnt/media` (disco `sdb`, físicamente distinto) o algo fuera del
servidor.

## Despliegue

```bash
# Copiar el script al servidor (mismo patrón que el resto del repo, junto a shopping-list-stack)
scp backup.py homeserver:~/shopping-list-stack/backup/

# Crontab de vdiaz -- una vez al día a las 03:00 (hora del servidor), fuera de horas de uso
crontab -e
# Añadir:
0 3 * * * /usr/bin/python3 /home/vdiaz/shopping-list-stack/backup/backup.py /mnt/storage/appdata/shopping-list/shopping_list.sqlite /mnt/storage/backups/shopping-list >> /home/vdiaz/shopping-list-stack/backup/backup.log 2>&1
```

## Restaurar un backup

```bash
# Parar el contenedor para que no escriba mientras se restaura
podman stop shopping-list

# Copiar el backup elegido encima del fichero real (revisa antes la fecha en el nombre)
cp /mnt/storage/backups/shopping-list/shopping_list-YYYY-MM-DD.sqlite \
   /mnt/storage/appdata/shopping-list/shopping_list.sqlite

podman start shopping-list
```
