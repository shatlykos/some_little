import abc


class MyAbstractClass(abc.ABC):
    
    @abc.abstractmethod
    def abstractmethod(self) -> None:
        pass


if __name__ == '__main__':
    print()
